from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Blueprint, current_app, jsonify, request

from app.core.data_quality import evaluate_contract_quality
from app.core.entry_signal import build_entry_signal
from app.core.greeks import fill_greeks
from app.core.open_snapshot import build_open_snapshot_dict
from app.core.strategy import derive_csp_candidate_row
from app.core.symbols import normalize_ticker_symbol
from app.core.types import Quote
from app.data.provider_yfinance import YFinanceProvider
from app.db.repo import Repo

bp_pool = Blueprint("pool", __name__, url_prefix="/api")

log = logging.getLogger(__name__)


_UNDERLYING_STATUSES = {"ACTIVE", "PAUSED", "ARCHIVED"}
_WATCH_RESTORABLE_STATUSES = {"WATCHING", "READY", "IGNORED", "EXPIRED"}
_WATCH_OPENABLE_STATUSES = {"WATCHING", "READY"}


def _json_error(message: str, status_code: int = 400, *, code: Optional[str] = None):
    payload: Dict[str, Any] = {"ok": False, "error": message}
    if code:
        payload["code"] = code
    return jsonify(payload), status_code


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError("invalid number")


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError("invalid integer")


def _normalize_instant(value: object) -> str:
    if value is None:
        raise ValueError("missing instant")
    if not isinstance(value, str):
        raise ValueError("instant must be a string")
    text = value.strip()
    if not text:
        raise ValueError("empty instant")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _option_snapshot_payload(option: Dict[str, Any], body: Dict[str, Any], watch_id: int) -> Dict[str, Any]:
    payload = dict(body)
    payload.update(
        {
            "option_pool_id": option.get("id"),
            "option_watchlist_id": watch_id,
            "open_candidate_id": option.get("latest_candidate_id"),
        }
    )
    for field in (
        "iv_rank",
        "iv",
        "delta",
        "theta",
        "vega",
        "spot",
        "dte",
        "annualized_roi",
        "score",
        "quality_grade",
        "quality_score",
        "quality_flags",
        "quote_age_seconds",
        "greeks_source",
        "iv_rank_source",
        "latest_entry_signal_id",
        "entry_signal_status",
        "entry_signal_score",
        "entry_signal_summary",
    ):
        if option.get(field) is not None:
            payload[field] = option.get(field)
    if option.get("latest_entry_signal_id") is not None:
        payload["entry_signal_id"] = option.get("latest_entry_signal_id")
    if isinstance(option.get("entry_signal"), dict):
        payload["entry_signal"] = option.get("entry_signal")
    return payload


def _watch_mutation_payload(data: Dict[str, Any], *, restore: bool = False) -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    for key in ("watch_reason", "ignore_reason", "notes"):
        if key in data:
            updates[key] = data.get(key)
    for key in ("target_premium", "target_score", "target_margin_buffer"):
        if key in data:
            updates[key] = _float_or_none(data.get(key))
    if "status" in data:
        status = str(data.get("status") or "").upper()
        if status not in _WATCH_RESTORABLE_STATUSES:
            raise ValueError("invalid status")
        updates["status"] = status
    elif restore:
        updates["status"] = "WATCHING"
    return updates


@bp_pool.route("/pool/underlyings", methods=["GET"])
def list_underlyings():
    repo: Repo = current_app.config["REPO"]
    rows = repo.list_pool_underlyings()
    rows.sort(key=lambda row: (0 if row.get("pool_status") == "ACTIVE" else 1, row.get("symbol") or ""))
    return jsonify({"underlyings": rows})


@bp_pool.route("/pool/underlyings/<symbol>", methods=["PATCH"])
def patch_underlying(symbol: str):
    repo: Repo = current_app.config["REPO"]
    sym = normalize_ticker_symbol(symbol)
    if not sym:
        return _json_error("invalid symbol")

    data = request.get_json(silent=True) or {}
    updates: Dict[str, Any] = {}
    if "pool_status" in data:
        status = str(data.get("pool_status") or "").upper()
        if status not in _UNDERLYING_STATUSES:
            return _json_error("invalid pool_status")
        updates["pool_status"] = status
    if "tags" in data:
        if not isinstance(data.get("tags"), list):
            return _json_error("tags must be a list")
        updates["tags"] = [str(tag).strip() for tag in data.get("tags") if str(tag).strip()]
    if "notes" in data:
        notes = data.get("notes")
        if notes is not None and not isinstance(notes, str):
            return _json_error("notes must be a string")
        updates["notes"] = notes
    row = repo.update_pool_underlying(sym, updates)
    if not row:
        return _json_error("underlying not found", 404, code="not_found")
    return jsonify(row)


@bp_pool.route("/pool/underlyings/<symbol>/pause", methods=["POST"])
def pause_underlying(symbol: str):
    repo: Repo = current_app.config["REPO"]
    row = repo.pause_pool_underlying(symbol)
    if not row:
        return _json_error("underlying not found", 404, code="not_found")
    return jsonify(row)


@bp_pool.route("/pool/underlyings/<symbol>/archive", methods=["POST"])
def archive_underlying(symbol: str):
    repo: Repo = current_app.config["REPO"]
    row = repo.archive_pool_underlying(symbol)
    if not row:
        return _json_error("underlying not found", 404, code="not_found")
    return jsonify(row)


@bp_pool.route("/pool/options", methods=["GET"])
def list_pool_options():
    repo: Repo = current_app.config["REPO"]
    try:
        rows = _query_option_pool(repo, request.args)
    except ValueError:
        return _json_error("invalid filter value")
    return jsonify({"options": rows})


def _query_option_pool(repo: Repo, args) -> List[Dict[str, Any]]:
    """Shared option-pool query (used by /pool/options and /screener/marks)."""
    return repo.list_option_pool(
        symbol=args.get("symbol") or None,
        status=args.get("status", "NEW,ACTIVE"),
        quality_grade=args.get("quality_grade") or None,
        min_score=_float_or_none(args.get("min_score")),
        min_dte=_int_or_none(args.get("min_dte")),
        max_dte=_int_or_none(args.get("max_dte")),
        entry_signal_status=args.get("entry_signal_status") or None,
        min_entry_signal_score=_int_or_none(args.get("min_entry_signal_score")),
    )


def _recompute_quality_and_status(
    result: Dict[str, Any],
    *,
    raw_contract: Any,
    filled_contract: Any,
    quote: Quote,
    settings: Dict[str, Any],
    today: date,
    previous_status: str,
) -> None:
    """Re-grade ``result`` against freshly-fetched contract data.

    The screener job is the single writer for ``option_pool.status``, but the
    refresh endpoint must still expose a status that matches the live data it
    just fetched — otherwise a contract whose quotes have recovered keeps
    flashing BLOCKED on the watch card even though every visible field
    (bid/ask/mid/spot/IV/margin_buffer) is healthy.

    Rules (in-memory only; does not write back to the DB):
      • Re-evaluate quality via ``evaluate_contract_quality``.
      • If quality_grade == "C" → ``status`` is forced to "BLOCKED".
      • If quality_grade is A/B/unknown and the persisted row was BLOCKED →
        promote to "ACTIVE" so the UI matches the visible data.
      • Otherwise keep the persisted non-terminal status (NEW/ACTIVE/STALE).
        EXPIRED / terminal statuses are left untouched here.
    """
    try:
        quality = evaluate_contract_quality(
            raw_contract,
            filled_contract,
            quote,
            settings,
            valuation_date=today,
        )
    except Exception as exc:
        log.debug("refresh_pool_option: quality re-grade skipped: %s", exc)
        return

    result["quality_grade"] = quality.quality_grade
    result["quality_score"] = quality.score
    result["quality_flags"] = list(quality.flags or [])
    if quality.quote_age_seconds is not None:
        result["quote_age_seconds"] = quality.quote_age_seconds
    if quality.greeks_source:
        result["greeks_source"] = quality.greeks_source
    if quality.iv_rank_source:
        result["iv_rank_source"] = quality.iv_rank_source

    if quality.quality_grade == "C":
        result["status"] = "BLOCKED"
    elif previous_status == "BLOCKED":
        result["status"] = "ACTIVE"
    # Other statuses (NEW / ACTIVE / STALE / EXPIRED) are preserved as-is.


def _apply_live_quote_to_option_row(
    opt: Dict[str, Any],
    spot: float,
    *,
    settings: Dict[str, Any],
    today: date,
    watch_row: Optional[Dict[str, Any]] = None,
) -> None:
    """Overlay fresh underlying spot on an option pool / watch.option row in-place.

    Recomputes margin_buffer / breakeven (spot-derived) and rebuilds the
    entry_signal so #screener 决策卡 风险卡 reflects the new market price.
    Premium / Greeks (mid, delta, theta, …) are *not* refreshed — they would
    require a full option-chain fetch per row, which is too expensive at the
    one-minute auto-refresh cadence and is not what the user requested.
    """
    opt["spot"] = float(spot)
    try:
        strike = float(opt.get("strike") or 0)
    except (TypeError, ValueError):
        strike = 0.0
    try:
        mid = float(opt.get("mid") or 0)
    except (TypeError, ValueError):
        mid = 0.0
    if strike > 0 and spot > 0:
        opt["margin_buffer"] = round((spot - strike) / spot, 4)
    if strike > 0 and mid > 0:
        opt["breakeven"] = round(strike - mid, 4)
    try:
        signal = build_entry_signal(
            opt,
            watch_row=watch_row,
            settings=settings,
            today=today,
        )
    except Exception as exc:
        log.debug("screener/marks: entry_signal rebuild skipped: %s", exc)
        return
    opt["entry_signal"] = signal
    opt["entry_signal_status"] = signal.get("status")
    opt["entry_signal_score"] = signal.get("decision_score")
    opt["entry_signal_summary"] = signal.get("summary")
    opt["entry_signal_generated_at"] = signal.get("generated_at")


def _collect_unique_symbols(*lists_of_rows: List[Dict[str, Any]]) -> List[str]:
    """Stable de-dup, uppercase, drop empties."""
    seen: Dict[str, None] = {}
    for rows in lists_of_rows:
        for row in rows:
            sym = (row.get("symbol") or "").upper().strip()
            if sym:
                seen.setdefault(sym, None)
    return list(seen.keys())


@bp_pool.route("/screener/marks", methods=["GET"])
def screener_marks():
    """Per-minute live-marks refresh for the #screener page.

    Mirrors `/api/positions/marks`: fetches fresh underlying spot for every
    symbol referenced by the active underlying pool / option pool / option
    watch grid, overlays it on the rows in-memory (no DB write — the
    screener job remains the sole writer), recomputes spot-derived fields,
    and rebuilds the entry_signal so 决策卡 reads the latest 行情 the next
    time the user opens it.
    """
    repo: Repo = current_app.config["REPO"]
    settings = repo.get_settings() or {}
    today = date.today()
    quoted_at = datetime.now(timezone.utc).isoformat()

    # --- Snapshot the same three lists the page already shows ---
    underlyings = repo.list_pool_underlyings()
    underlyings.sort(
        key=lambda row: (
            0 if row.get("pool_status") == "ACTIVE" else 1,
            row.get("symbol") or "",
        )
    )

    try:
        options = _query_option_pool(repo, request.args)
    except ValueError:
        return _json_error("invalid filter value")

    watch_status = request.args.get("watch_status") or "WATCHING,READY"
    watches = repo.list_option_watches(status=watch_status)

    # --- Collect symbols that justify a live quote ---
    active_underlyings = [u for u in underlyings if u.get("pool_status") == "ACTIVE"]
    watch_option_rows = [w.get("option") or {} for w in watches]
    symbols = _collect_unique_symbols(active_underlyings, options, watch_option_rows)

    # --- Pull fresh quotes (per-symbol failures are isolated) ---
    provider = YFinanceProvider()
    quotes: Dict[str, float] = {}
    errors: Dict[str, str] = {}
    for sym in symbols:
        try:
            quotes[sym] = float(provider.get_quote(sym).spot)
        except Exception as exc:
            errors[sym] = str(exc)[:240]

    # --- Apply to underlyings (cosmetic: live_spot is additive) ---
    for u in underlyings:
        sym = (u.get("symbol") or "").upper()
        if sym in quotes:
            u["live_spot"] = quotes[sym]
            u["live_quoted_at"] = quoted_at

    # --- Apply to option pool rows + rebuild signal ---
    for opt in options:
        sym = (opt.get("symbol") or "").upper()
        if sym in quotes:
            _apply_live_quote_to_option_row(
                opt, quotes[sym], settings=settings, today=today
            )

    # --- Apply to watch.option rows + rebuild signal (watch_row context) ---
    for watch in watches:
        opt = watch.get("option") or {}
        sym = (opt.get("symbol") or "").upper()
        if sym in quotes:
            _apply_live_quote_to_option_row(
                opt,
                quotes[sym],
                settings=settings,
                today=today,
                watch_row=watch,
            )
            watch["option"] = opt

    return jsonify(
        {
            "schema": "screener_marks_v1",
            "quoted_at": quoted_at,
            "underlyings": underlyings,
            "options": options,
            "watches": watches,
            "errors": errors,
        }
    )


@bp_pool.route("/pool/options/<int:option_pool_id>/entry-signal", methods=["GET"])
def get_pool_option_entry_signal(option_pool_id: int):
    repo: Repo = current_app.config["REPO"]
    option = repo.get_option_pool(option_pool_id)
    if not option:
        return _json_error("option pool row not found", 404, code="not_found")
    return jsonify({"entry_signal": repo.get_latest_entry_signal(option_pool_id)})


@bp_pool.route("/pool/options/<int:option_pool_id>/refresh", methods=["GET"])
def refresh_pool_option(option_pool_id: int):
    """On-demand full refresh of a single option row (Premium + Greeks + spot + entry_signal).

    Unlike ``/api/screener/marks`` (which only overlays underlying spot),
    this endpoint fetches the full option chain for the specific expiration/strike
    so bid/ask/mid, delta/theta/vega, iv, and all derived metrics are all current
    when the user opens the 决策卡.

    Optional query param ``watch_id``: when supplied the matching watch row is
    loaded and passed to ``build_entry_signal`` for watch-context reasons (mirrors
    the watch-grid 决策卡 flow).
    """
    repo: Repo = current_app.config["REPO"]
    option = repo.get_option_pool(option_pool_id)
    if not option:
        return _json_error("option pool row not found", 404, code="not_found")

    watch_row: Optional[Dict[str, Any]] = None
    watch_id_raw = request.args.get("watch_id")
    if watch_id_raw:
        try:
            wid = int(watch_id_raw)
        except (TypeError, ValueError):
            return _json_error("invalid watch_id")
        watch_row = repo.get_option_watch(wid)

    settings = repo.get_settings() or {}
    today = date.today()
    symbol = (option.get("symbol") or "").upper()
    expiration_str = option.get("expiration") or ""
    try:
        strike = float(option.get("strike") or 0)
    except (TypeError, ValueError):
        strike = 0.0
    right = option.get("right") or "P"

    if not symbol or not expiration_str or strike <= 0:
        return _json_error("option pool row missing required fields")

    try:
        expiration = date.fromisoformat(expiration_str)
    except ValueError:
        return _json_error("invalid expiration date")

    if (expiration - today).days <= 0:
        return _json_error("option has expired", 400, code="expired")

    quoted_at = datetime.now(timezone.utc).isoformat()
    provider = YFinanceProvider()

    # --- Fresh underlying spot (required; abort if unavailable) ---
    try:
        quote_obj = provider.get_quote(symbol)
        spot = float(quote_obj.spot)
    except Exception as exc:
        log.warning("refresh_pool_option: spot fetch failed for %s: %s", symbol, exc)
        return _json_error(f"spot quote failed: {str(exc)[:200]}", 502, code="quote_failed")

    result = dict(option)
    chain_refreshed = False
    risk_free_rate = float(settings.get("risk_free_rate", 0.045) or 0.045)

    # --- Full option chain refresh (best-effort; falls back to spot-only) ---
    try:
        contracts = provider.get_option_chain(
            symbol, expiration, right,
            anchor_strike=strike,
            underlying_spot=spot,
        )
        match = next((c for c in contracts if abs(c.strike - strike) < 0.01), None)
        if match:
            raw_match = match
            # Fill missing Greeks via Black-Scholes so a provider that omits
            # delta/theta/vega (yfinance for deep-OTM long-dated puts) does not
            # leave the screener decision card with NULL risk fields and a stale
            # BLOCKED badge.  Mirrors the screener job's chain post-processing.
            try:
                match = fill_greeks(match, spot, risk_free_rate, valuation_date=today)
            except Exception as exc:
                log.debug(
                    "refresh_pool_option: fill_greeks skipped for %s %s %s: %s",
                    symbol, expiration_str, strike, exc,
                )
            fresh_quote = Quote(
                symbol=symbol,
                spot=spot,
                asof=quote_obj.asof,
                iv_rank=option.get("iv_rank"),  # keep stored iv_rank; history is too slow on demand
            )
            fresh = derive_csp_candidate_row(match, fresh_quote, settings)
            if fresh:
                for key in (
                    "bid", "ask", "mid", "spot", "iv",
                    "delta", "theta", "vega", "gamma",
                    "dte", "annualized_roi", "spread_pct",
                    "breakeven", "margin_buffer", "score", "open_interest",
                ):
                    if fresh.get(key) is not None:
                        result[key] = fresh[key]
                if match.quote_age_seconds is not None:
                    result["quote_age_seconds"] = match.quote_age_seconds
                chain_refreshed = True
            else:
                # Contract found but not enough data for full candidate row;
                # overlay raw fields directly.
                for key in ("bid", "ask", "iv", "delta", "theta", "vega", "gamma", "open_interest"):
                    val = getattr(match, key, None)
                    if val is not None:
                        result[key] = val
                if match.mid is not None:
                    result["mid"] = match.mid
                if (match.expiration - today).days > 0:
                    result["dte"] = (match.expiration - today).days
                result["spot"] = spot
                if strike > 0 and spot > 0:
                    result["margin_buffer"] = round((spot - strike) / spot, 4)
                mid_val = float(result.get("mid") or 0)
                if strike > 0 and mid_val > 0:
                    result["breakeven"] = round(strike - mid_val, 4)

            # Re-grade data quality from the freshly-fetched contract so a
            # previously-BLOCKED row whose quotes have recovered is not stuck
            # behind a stale status/quality_grade carried over from `option`.
            # See `_recompute_quality_and_status` for the BLOCKED↔ACTIVE rules.
            _recompute_quality_and_status(
                result,
                raw_contract=raw_match,
                filled_contract=match,
                quote=fresh_quote,
                settings=settings,
                today=today,
                previous_status=str(option.get("status") or "").upper(),
            )
    except Exception as exc:
        log.warning(
            "refresh_pool_option: chain fetch failed for %s %s: %s",
            symbol, expiration_str, exc,
        )
        # Spot-only fallback
        result["spot"] = spot
        if strike > 0 and spot > 0:
            result["margin_buffer"] = round((spot - strike) / spot, 4)
        mid_val = float(result.get("mid") or 0)
        if strike > 0 and mid_val > 0:
            result["breakeven"] = round(strike - mid_val, 4)

    # --- Rebuild entry_signal with all fresh data ---
    try:
        signal = build_entry_signal(
            result,
            watch_row=watch_row,
            settings=settings,
            today=today,
        )
        result["entry_signal"] = signal
        result["entry_signal_status"] = signal.get("status")
        result["entry_signal_score"] = signal.get("decision_score")
        result["entry_signal_summary"] = signal.get("summary")
        result["entry_signal_generated_at"] = signal.get("generated_at")
    except Exception as exc:
        log.debug("refresh_pool_option: entry_signal rebuild failed: %s", exc)

    return jsonify(
        {
            "schema": "option_refresh_v1",
            "quoted_at": quoted_at,
            "option": result,
            "chain_refreshed": chain_refreshed,
        }
    )


@bp_pool.route("/watch/options", methods=["GET"])
def list_option_watches():
    repo: Repo = current_app.config["REPO"]
    rows = repo.list_option_watches(status=request.args.get("status") or None)
    return jsonify({"watches": rows})


@bp_pool.route("/watch/options", methods=["POST"])
def create_option_watch():
    repo: Repo = current_app.config["REPO"]
    data = request.get_json(silent=True) or {}
    try:
        option_pool_id = int(data.get("option_pool_id"))
    except (TypeError, ValueError):
        return _json_error("missing or invalid option_pool_id")
    try:
        payload = _watch_mutation_payload(data, restore=True)
    except ValueError:
        return _json_error("invalid watch payload")
    payload["option_pool_id"] = option_pool_id
    watch = repo.create_option_watch(payload)
    if not watch:
        return _json_error("option pool row not found", 404, code="not_found")
    return jsonify(watch), 201


@bp_pool.route("/watch/options/<int:watch_id>", methods=["PATCH"])
def patch_option_watch(watch_id: int):
    repo: Repo = current_app.config["REPO"]
    data = request.get_json(silent=True) or {}
    try:
        payload = _watch_mutation_payload(data, restore=True)
    except ValueError:
        return _json_error("invalid watch payload")
    watch = repo.update_option_watch(watch_id, payload)
    if not watch:
        return _json_error("watch not found", 404, code="not_found")
    return jsonify(watch)


@bp_pool.route("/watch/options/<int:watch_id>/ignore", methods=["POST"])
def ignore_option_watch(watch_id: int):
    repo: Repo = current_app.config["REPO"]
    data = request.get_json(silent=True) or {}
    watch = repo.ignore_option_watch(watch_id, reason=data.get("ignore_reason"))
    if not watch:
        return _json_error("watch not found", 404, code="not_found")
    return jsonify(watch)


@bp_pool.route("/watch/options/<int:watch_id>/open", methods=["POST"])
def open_option_watch(watch_id: int):
    repo: Repo = current_app.config["REPO"]
    watch = repo.get_option_watch(watch_id)
    if not watch:
        return _json_error("watch not found", 404, code="not_found")
    if str(watch.get("status") or "").upper() not in _WATCH_OPENABLE_STATUSES:
        return _json_error("watch status is not openable")

    option = watch.get("option") or repo.get_option_pool(int(watch.get("option_pool_id") or 0))
    if not option:
        return _json_error("option pool row not found", 404, code="not_found")
    if str(option.get("status") or "").upper() in {"EXPIRED", "BLOCKED"}:
        return _json_error("option status is not openable")

    body = request.get_json(silent=True) or {}
    for field in ("open_premium", "contracts"):
        if field not in body:
            return _json_error(f"missing field: {field}")
    try:
        contracts = int(body.get("contracts"))
        open_premium = float(body.get("open_premium"))
    except (TypeError, ValueError):
        return _json_error("invalid open_premium or contracts")
    if contracts <= 0:
        return _json_error("contracts must be positive")
    if open_premium < 0:
        return _json_error("open_premium must be non-negative")

    open_at_raw = body.get("open_at")
    try:
        open_at = _normalize_instant(open_at_raw) if open_at_raw else datetime.now(timezone.utc).isoformat()
    except ValueError:
        return _json_error("invalid open_at")

    pos = {
        "symbol": option["symbol"],
        "expiration": option["expiration"],
        "strike": float(option["strike"]),
        "contracts": contracts,
        "open_at": open_at,
        "open_premium": open_premium,
        "open_candidate_id": option.get("latest_candidate_id"),
        "state": "OPEN",
        "notes": body.get("notes") if "notes" in body else watch.get("notes"),
    }
    position_id = repo.insert_position(pos)
    try:
        snapshot_payload = _option_snapshot_payload(option, body, watch_id)
        snapshot = build_open_snapshot_dict(repo, pos, snapshot_payload)
        if snapshot:
            repo.save_open_snapshot(position_id, snapshot)
    except Exception as exc:
        log.warning("watch open_snapshot capture failed: %s", exc)

    repo.mark_option_watch_opened(
        watch_id,
        {
            "status": "OPENED",
            "reason": "user_opened",
            "position_id": position_id,
        },
    )
    return jsonify({"id": position_id, **pos, "option_pool_id": option.get("id"), "option_watchlist_id": watch_id}), 201
