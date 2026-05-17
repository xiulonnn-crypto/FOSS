from __future__ import annotations

import json as _json
import logging as _logging
from datetime import date, datetime, time, timezone
from typing import Dict, Optional, Tuple
from flask import Blueprint, current_app, jsonify, request

from app.core.exit_signal import build_exit_signal
from app.core.massive_closed_enrichment import enrich_closed_position_open_snapshot_massive
from app.core.pnl_excursion_intraday import enrich_closed_position_intraday_bs
from app.core.open_snapshot import build_open_snapshot_dict
from app.core.position_mark import mark_short_put_position
from app.core.radar_snapshot import append_radar_snapshot_from_mark
from app.core.settlement import calc_realized_pnl
from app.core.symbols import normalize_ticker_symbol
from app.core.time_et import APP_TZ
from app.core.types import Quote
from app.data.provider_yfinance import YFinanceProvider
from app.db.repo import Repo

bp_positions = Blueprint("positions", __name__, url_prefix="/api")

log = _logging.getLogger(__name__)


def _normalize_instant(value: object) -> str:
    """Parse ISO-8601 or JS toISOString() into normalized UTC ISO for SQLite."""
    if value is None:
        raise ValueError("missing instant")
    if not isinstance(value, str):
        raise ValueError("instant must be a string")
    s = value.strip()
    if not s:
        raise ValueError("empty instant")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _capture_open_snapshot(repo: Repo, position_id: int, request_data: dict, pos: dict) -> None:
    """
    Gather entry environment metrics and store them as open_snapshot.
    Best-effort: individual failures are caught and skipped.
    """
    snapshot = build_open_snapshot_dict(repo, pos, request_data)
    if snapshot:
        repo.save_open_snapshot(position_id, snapshot)


def _best_effort_close_radar_snapshot(
    repo: Repo, position_id: int, close_ts_iso: str, pos: dict
) -> Optional[dict]:
    """Final radar row at close; must match positions.close_at for attribution filters."""
    try:
        settings = repo.get_settings()
        risk_free = float(settings.get("risk_free_rate", 0.045))
        provider = YFinanceProvider()
        mark = mark_short_put_position(pos, provider, risk_free)
        snapshot_id = append_radar_snapshot_from_mark(
            repo, position_id, close_ts_iso, mark, signals=None
        )
        if not snapshot_id:
            log.warning(
                "close: radar snapshot skipped (incomplete mark) position_id=%s",
                position_id,
            )
        else:
            mark["radar_snapshot_id"] = snapshot_id
        return mark
    except Exception as exc:
        log.warning(
            "close: radar snapshot failed (non-fatal) position_id=%s: %s",
            position_id,
            exc,
        )
        return None


def _resolve_exit_signal_for_close(
    repo: Repo,
    pos: dict,
    mark: Optional[dict],
    exit_signal_id_raw: object = None,
) -> Tuple[Optional[dict], Optional[int], Optional[str]]:
    signal: Optional[dict] = None
    exit_signal_id: Optional[int] = None
    if exit_signal_id_raw not in (None, ""):
        try:
            exit_signal_id = int(exit_signal_id_raw)
        except (TypeError, ValueError):
            return None, None, "invalid exit_signal_id"
        signal = repo.get_exit_signal(exit_signal_id)
        if not signal or int(signal.get("position_id") or -1) != int(pos.get("id")):
            return None, None, "invalid exit_signal_id"
    else:
        signal = repo.get_latest_exit_signal(int(pos["id"]))
        if signal:
            exit_signal_id = signal.get("id") or signal.get("exit_signal_id")

    if signal is None and mark is not None:
        signal = build_exit_signal(pos, mark, repo.get_settings())
        exit_signal_id = None
    return signal, exit_signal_id, None


def _close_snapshot(
    *,
    close_at: str,
    close_premium: float | None,
    close_reason: str,
    close_notes: object,
    realized_pnl: float,
    mark: Optional[dict],
    exit_signal: Optional[dict],
    exit_signal_id: Optional[int],
) -> dict:
    return {
        "schema": "position_close_snapshot_v1",
        "closed_at": close_at,
        "close_premium": close_premium,
        "selected_close_reason": close_reason,
        "close_notes": close_notes,
        "realized_pnl": realized_pnl,
        "exit_signal_id": exit_signal_id,
        "exit_signal": exit_signal,
        "mark": mark,
    }


def _cached_position_mark(pos: dict) -> dict:
    signal = pos.get("exit_signal_payload") or {}
    metrics = signal.get("metrics") if isinstance(signal, dict) else {}
    source = signal.get("source") if isinstance(signal, dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    source = source if isinstance(source, dict) else {}
    if not metrics:
        return {"mark_pending": True, "mark_basis": "pending"}
    mid = metrics.get("current_mid")
    return {
        "cached": True,
        "spot": metrics.get("spot"),
        "option_mid": mid,
        "mark_basis": source.get("mark_basis") or "cached_exit_signal",
        "delta": metrics.get("delta"),
        "margin_buffer": metrics.get("margin_buffer"),
        "pnl_pct": metrics.get("pnl_pct"),
        "unrealized_pnl_usd": metrics.get("unrealized_pnl_usd"),
    }


@bp_positions.route("/positions", methods=["GET"])
def list_positions():
    repo: Repo = current_app.config["REPO"]
    state = request.args.get("state")
    return jsonify(repo.list_positions(state=state))


@bp_positions.route("/positions/marks", methods=["GET"])
def list_positions_with_marks():
    """OPEN positions with live spot / option mid / unrealized P&L (same math as radar)."""
    repo: Repo = current_app.config["REPO"]
    settings = repo.get_settings()
    risk_free = float(settings.get("risk_free_rate", 0.045))
    positions = repo.list_positions(state="OPEN")
    quoted_at = datetime.now(timezone.utc).isoformat()
    action_log_counts = repo.count_position_action_logs_by_position_ids(
        [int(pos["id"]) for pos in positions if pos.get("id") is not None]
    )
    if request.args.get("fast") in {"1", "true", "yes"}:
        out_rows = []
        for pos in positions:
            row = dict(pos)
            row["mark"] = _cached_position_mark(row)
            latest_signal = row.get("exit_signal_payload")
            row["exit_signal"] = latest_signal
            row["latest_exit_signal_id"] = row.get("latest_exit_signal_id")
            row["action_logs_count"] = action_log_counts.get(int(row["id"]), 0)
            out_rows.append(row)
        return jsonify({
            "quoted_at": quoted_at,
            "positions": out_rows,
            "mode": "fast",
        })

    provider = YFinanceProvider()

    symbols: list[str] = []
    seen: set[str] = set()
    for pos in positions:
        sym = (pos.get("symbol") or "").upper()
        if sym and sym not in seen:
            seen.add(sym)
            symbols.append(sym)

    prefetched: dict[str, Quote | None] = {}
    for sym in symbols:
        try:
            prefetched[sym] = provider.get_quote(sym)
        except Exception:
            prefetched[sym] = None

    chain_cache: Dict[Tuple[str, str], Optional[list]] = {}

    def _get_prefetched_chain(pos: dict, quote: Optional[Quote]) -> Optional[list]:
        sym = (pos.get("symbol") or "").upper()
        exp_raw = str(pos.get("expiration") or "").strip()[:10]
        if not sym or not exp_raw or quote is None:
            return None
        key = (sym, exp_raw)
        if key not in chain_cache:
            try:
                exp_date = date.fromisoformat(exp_raw)
                chain_cache[key] = provider.get_option_chain(
                    sym,
                    exp_date,
                    right="P",
                    underlying_spot=float(quote.spot),
                )
            except Exception:
                chain_cache[key] = None
        return chain_cache.get(key)

    out_rows = []
    for pos in positions:
        row = dict(pos)
        sym = (pos.get("symbol") or "").upper()
        pq = prefetched.get(sym)
        try:
            mark = mark_short_put_position(
                row,
                provider,
                risk_free,
                prefetched_quote=pq,
                prefetched_chain=_get_prefetched_chain(row, pq),
            )
        except TypeError as exc:
            if "prefetched_chain" not in str(exc):
                raise
            mark = mark_short_put_position(
                row,
                provider,
                risk_free,
                prefetched_quote=pq,
            )
        row["mark"] = mark
        live_exit_signal = build_exit_signal(row, mark, settings)
        latest_exit_signal = row.get("exit_signal_payload")
        row["exit_signal"] = (
            latest_exit_signal
            if latest_exit_signal and live_exit_signal.get("action") == "UNKNOWN"
            else live_exit_signal
        )
        row["latest_exit_signal_id"] = row.get("latest_exit_signal_id")
        row["action_logs_count"] = action_log_counts.get(int(row["id"]), 0)
        out_rows.append(row)
    return jsonify({"quoted_at": quoted_at, "positions": out_rows, "mode": "live"})


@bp_positions.route("/positions", methods=["POST"])
def create_position():
    repo: Repo = current_app.config["REPO"]
    data = request.get_json(silent=True) or {}
    required = ["symbol", "expiration", "strike", "contracts", "open_premium"]
    for field in required:
        if field not in data:
            return jsonify({"error": f"missing field: {field}"}), 400

    sym = normalize_ticker_symbol(str(data["symbol"]))
    if not sym:
        return jsonify({"error": "invalid symbol"}), 400

    open_at_raw = data.get("open_at")
    if open_at_raw:
        try:
            open_at_str = _normalize_instant(open_at_raw)
        except ValueError:
            return jsonify({"error": "invalid open_at"}), 400
    else:
        open_at_str = datetime.now(timezone.utc).isoformat()

    pos = {
        "symbol": sym,
        "expiration": data["expiration"],
        "strike": float(data["strike"]),
        "contracts": int(data["contracts"]),
        "open_at": open_at_str,
        "open_premium": float(data["open_premium"]),
        "open_candidate_id": data.get("open_candidate_id"),
        "state": "OPEN",
        "notes": data.get("notes"),
    }
    pid = repo.insert_position(pos)

    try:
        _capture_open_snapshot(repo, pid, data, pos)
    except Exception as _snap_exc:
        _logging.getLogger(__name__).warning("open_snapshot capture failed: %s", _snap_exc)

    return jsonify({"id": pid, **pos}), 201


@bp_positions.route("/positions/<int:position_id>", methods=["GET"], strict_slashes=False)
def get_position(position_id: int):
    repo: Repo = current_app.config["REPO"]
    pos = repo.get_position(position_id)
    if not pos:
        return jsonify({"error": "not found"}), 404
    return jsonify(pos)


@bp_positions.route(
    "/positions/<int:position_id>/exit-signal", methods=["GET"], strict_slashes=False
)
def get_position_exit_signal(position_id: int):
    repo: Repo = current_app.config["REPO"]
    pos = repo.get_position(position_id)
    if not pos:
        return jsonify({"error": "not found"}), 404
    latest = repo.get_latest_exit_signal(position_id)
    if latest:
        return jsonify(latest)

    settings = repo.get_settings()
    risk_free = float(settings.get("risk_free_rate", 0.045))
    provider = YFinanceProvider()
    mark = mark_short_put_position(pos, provider, risk_free)
    return jsonify(build_exit_signal(pos, mark, settings))


@bp_positions.route(
    "/positions/<int:position_id>/action-logs", methods=["GET"], strict_slashes=False
)
def list_position_action_logs(position_id: int):
    repo: Repo = current_app.config["REPO"]
    if not repo.get_position(position_id):
        return jsonify({"error": "not found"}), 404
    return jsonify(repo.list_position_action_logs(position_id))


@bp_positions.route(
    "/positions/<int:position_id>/action-log", methods=["POST"], strict_slashes=False
)
def create_position_action_log(position_id: int):
    repo: Repo = current_app.config["REPO"]
    if not repo.get_position(position_id):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    action_type = str(data.get("action_type") or "CONTINUE").upper()
    if action_type not in {"CONTINUE", "CLOSE_CONFIRMED"}:
        return jsonify({"error": "invalid action_type"}), 400
    reason = str(data.get("reason") or "").strip()
    if action_type == "CONTINUE" and not reason:
        return jsonify({"error": "missing reason"}), 400
    exit_signal_id = data.get("exit_signal_id")
    if exit_signal_id in ("", None):
        exit_signal_id = None
    else:
        try:
            exit_signal_id = int(exit_signal_id)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid exit_signal_id"}), 400
    log_id = repo.insert_position_action_log(
        position_id,
        action_type,
        reason=reason or None,
        notes=data.get("notes"),
        exit_signal_id=exit_signal_id,
    )
    return jsonify({"id": log_id, "ok": True}), 201


@bp_positions.route(
    "/positions/<int:position_id>", methods=["PATCH", "PUT"], strict_slashes=False
)
def patch_position(position_id: int):
    repo: Repo = current_app.config["REPO"]
    pos = repo.get_position(position_id)
    if not pos:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(silent=True) or {}
    is_open = pos.get("state") == "OPEN"
    updates: dict = {}

    def _fkey(name: str) -> bool:
        return name in data and data[name] is not None

    if _fkey("symbol"):
        updates["symbol"] = normalize_ticker_symbol(str(data["symbol"]))
        if not updates["symbol"]:
            return jsonify({"error": "invalid symbol"}), 400
    if _fkey("expiration"):
        updates["expiration"] = str(data["expiration"]).strip()
    if _fkey("strike"):
        updates["strike"] = float(data["strike"])
    if _fkey("contracts"):
        updates["contracts"] = int(data["contracts"])
    if _fkey("open_premium"):
        updates["open_premium"] = float(data["open_premium"])
    if "notes" in data:
        updates["notes"] = data.get("notes")
    if _fkey("open_at"):
        try:
            updates["open_at"] = _normalize_instant(data["open_at"])
        except ValueError:
            return jsonify({"error": "invalid open_at"}), 400

    if not is_open:
        if "close_at" in data and data["close_at"] is not None:
            try:
                updates["close_at"] = _normalize_instant(data["close_at"])
            except ValueError:
                return jsonify({"error": "invalid close_at"}), 400
        if "close_premium" in data and data["close_premium"] is not None:
            updates["close_premium"] = float(data["close_premium"])
        if "close_reason" in data and data["close_reason"] is not None:
            updates["close_reason"] = str(data["close_reason"]).strip()

        if "realized_pnl" in data and data["realized_pnl"] is not None:
            updates["realized_pnl"] = float(data["realized_pnl"])
        elif any(k in updates for k in ("open_premium", "close_premium", "contracts")):
            settings = repo.get_settings()
            fee = float(settings.get("fees", {}).get("usd_per_contract", 1.0))
            op = float(updates.get("open_premium", pos["open_premium"]))
            cp_raw = updates.get("close_premium", pos["close_premium"])
            cp = float(cp_raw if cp_raw is not None else 0)
            ct = int(updates.get("contracts", pos["contracts"]))
            cr = str(updates.get("close_reason", pos.get("close_reason") or ""))
            st = str(pos.get("state") or "")
            if (cr == "expired_otm" or st == "EXPIRED_OTM") and cp == 0.0:
                fee_legs = 1
            else:
                fee_legs = 2
            updates["realized_pnl"] = calc_realized_pnl(op, cp, ct, fee, fee_legs=fee_legs)
    else:
        if any(k in data for k in ("close_at", "close_premium", "close_reason", "realized_pnl")):
            bad = [k for k in ("close_at", "close_premium", "close_reason", "realized_pnl") if k in data]
            return jsonify({"error": f"cannot patch {bad} on OPEN position"}), 400

    if not updates:
        return jsonify({"error": "no valid fields to update"}), 400

    repo.update_position_fields(position_id, updates)
    out = repo.get_position(position_id)
    return jsonify(out or {"ok": True})


@bp_positions.route("/positions/<int:position_id>/close", methods=["POST"], strict_slashes=False)
def close_position(position_id: int):
    repo: Repo = current_app.config["REPO"]
    pos = repo.get_position(position_id)
    if not pos:
        return jsonify({"error": "not found"}), 404
    if pos["state"] != "OPEN":
        return jsonify({"error": "position is not OPEN"}), 400

    data = request.get_json(silent=True) or {}
    settings = repo.get_settings()
    fee = float(settings.get("fees", {}).get("usd_per_contract", 1.0))

    if data.get("expiry_auto") is True:
        exp_raw = pos.get("expiration")
        if not exp_raw or not isinstance(exp_raw, str):
            return jsonify({"error": "missing expiration on position"}), 400
        try:
            exp_d = date.fromisoformat(exp_raw.strip()[:10])
        except ValueError:
            return jsonify({"error": "invalid expiration on position"}), 400
        close_dt = datetime.combine(exp_d, time(16, 0), tzinfo=APP_TZ)
        close_at_str = close_dt.astimezone(timezone.utc).isoformat()
        close_premium = 0.0
        close_reason = "expired_otm"
        pnl = calc_realized_pnl(
            float(pos["open_premium"]),
            close_premium,
            int(pos["contracts"]),
            fee,
            fee_legs=1,
        )
        close_mark = _best_effort_close_radar_snapshot(repo, position_id, close_at_str, pos)
        exit_signal, exit_signal_id, exit_error = _resolve_exit_signal_for_close(
            repo, pos, close_mark, data.get("exit_signal_id")
        )
        if exit_error:
            return jsonify({"error": exit_error}), 400
        repo.close_position(
            position_id, "EXPIRED_OTM", close_premium, close_reason, pnl, close_at=close_at_str
        )
        repo.save_position_close_snapshot(
            position_id,
            _close_snapshot(
                close_at=close_at_str,
                close_premium=close_premium,
                close_reason=close_reason,
                close_notes=data.get("close_notes"),
                realized_pnl=pnl,
                mark=close_mark,
                exit_signal=exit_signal,
                exit_signal_id=exit_signal_id,
            ),
            close_signal_id=exit_signal_id,
        )
        repo.insert_position_action_log(
            position_id,
            "CLOSE_CONFIRMED",
            reason=close_reason,
            notes=data.get("close_notes"),
            exit_signal_id=exit_signal_id,
        )
        try:
            enrich_closed_position_open_snapshot_massive(repo, position_id)
        except Exception as exc:
            log.warning("close: massive enrich failed (non-fatal) position_id=%s: %s", position_id, exc)
        try:
            enrich_closed_position_intraday_bs(repo, position_id)
        except Exception as exc:
            log.warning("close: intraday_bs enrich failed (non-fatal) position_id=%s: %s", position_id, exc)
        return jsonify({"ok": True, "realized_pnl": pnl})

    close_premium = float(data.get("close_premium", 0))
    close_reason = data.get("close_reason", "manual")
    pnl = calc_realized_pnl(
        float(pos["open_premium"]),
        close_premium,
        int(pos["contracts"]),
        fee,
        fee_legs=2,
    )
    if data.get("close_at"):
        try:
            close_ts = _normalize_instant(data["close_at"])
        except ValueError:
            return jsonify({"error": "invalid close_at"}), 400
    else:
        close_ts = datetime.now(timezone.utc).isoformat()
    close_mark = _best_effort_close_radar_snapshot(repo, position_id, close_ts, pos)
    exit_signal, exit_signal_id, exit_error = _resolve_exit_signal_for_close(
        repo, pos, close_mark, data.get("exit_signal_id")
    )
    if exit_error:
        return jsonify({"error": exit_error}), 400
    repo.close_position(
        position_id, "CLOSED_EARLY", close_premium, close_reason, pnl, close_at=close_ts
    )
    repo.save_position_close_snapshot(
        position_id,
        _close_snapshot(
            close_at=close_ts,
            close_premium=close_premium,
            close_reason=close_reason,
            close_notes=data.get("close_notes"),
            realized_pnl=pnl,
            mark=close_mark,
            exit_signal=exit_signal,
            exit_signal_id=exit_signal_id,
        ),
        close_signal_id=exit_signal_id,
    )
    repo.insert_position_action_log(
        position_id,
        "CLOSE_CONFIRMED",
        reason=close_reason,
        notes=data.get("close_notes"),
        exit_signal_id=exit_signal_id,
    )
    try:
        enrich_closed_position_open_snapshot_massive(repo, position_id)
    except Exception as exc:
        log.warning("close: massive enrich failed (non-fatal) position_id=%s: %s", position_id, exc)
    try:
        enrich_closed_position_intraday_bs(repo, position_id)
    except Exception as exc:
        log.warning("close: intraday_bs enrich failed (non-fatal) position_id=%s: %s", position_id, exc)
    return jsonify({"ok": True, "realized_pnl": pnl})


@bp_positions.route(
    "/positions/<int:position_id>/notes", methods=["PATCH"], strict_slashes=False
)
def patch_notes(position_id: int):
    repo: Repo = current_app.config["REPO"]
    data = request.get_json(silent=True) or {}
    note = data.get("notes", "")
    import sqlite3
    with repo._connect() as con:
        con.execute("UPDATE positions SET notes=? WHERE id=?", (note, position_id))
    return jsonify({"ok": True})


@bp_positions.route(
    "/positions/<int:position_id>/radar", methods=["GET"], strict_slashes=False
)
def radar_history(position_id: int):
    repo: Repo = current_app.config["REPO"]
    limit = int(request.args.get("limit", 100))
    snaps = repo.list_radar_snapshots(position_id, limit=limit)
    return jsonify(snaps)
