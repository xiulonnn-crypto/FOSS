from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Optional

from flask import Blueprint, current_app, jsonify, request

from app.core.data_quality import evaluate_contract_quality, infer_quality_from_candidate_snapshot
from app.core.entry_signal import build_entry_signal
from app.core.greeks import fill_greeks
from app.core.strategy import compute_iv_rank, derive_csp_candidate_row
from app.core.symbols import normalize_ticker_symbol
from app.core.types import OptionContract, Quote
from app.data.provider_yfinance import YFinanceProvider
from app.db.repo import Repo, _now_utc
from app.jobs.job_screener import run_screener

log = logging.getLogger(__name__)

bp_scan = Blueprint("scan", __name__, url_prefix="/api")


def _pick_put_near_strike(
    contracts: list, target_strike: float
) -> Optional[OptionContract]:
    best: Optional[OptionContract] = None
    best_d = 1e18
    for c in contracts:
        if c.right != "P":
            continue
        d = abs(float(c.strike) - float(target_strike))
        if d < best_d:
            best_d = d
            best = c
    if best is None:
        return None
    tol = max(5e-3, abs(target_strike) * 1e-10)
    if best_d > tol:
        return None
    return best


def _candidate_wire(row: dict, settings: Optional[dict] = None) -> dict:
    out = dict(row)
    grade = str(out.get("quality_grade") or "").strip().lower()
    if grade in {"", "unknown"}:
        inferred = infer_quality_from_candidate_snapshot(out, settings or {})
        if inferred is not None:
            out.update(inferred.as_flat_fields())
    flags = out.get("quality_flags")
    if flags is None:
        flags = []
    elif not isinstance(flags, list):
        flags = [str(flags)]
    out["quality_flags"] = flags
    out["quality_grade"] = out.get("quality_grade") or "unknown"
    out["data_quality"] = {
        "grade": out["quality_grade"],
        "score": out.get("quality_score"),
        "flags": flags,
        "quote_age_seconds": out.get("quote_age_seconds"),
        "greeks_source": out.get("greeks_source"),
        "iv_rank_source": out.get("iv_rank_source"),
    }
    return out


def _filter_openable_candidates(candidates: list, settings: dict) -> list:
    """When entry_signal.openable_only, keep rows with OPENABLE status only."""
    entry_cfg = settings.get("entry_signal") or {}
    if not entry_cfg.get("openable_only"):
        return candidates
    filtered = []
    for row in candidates:
        sig = row.get("entry_signal")
        status = None
        if isinstance(sig, dict):
            status = sig.get("status")
        if status is None:
            status = row.get("entry_signal_status")
        if str(status or "").upper() == "OPENABLE":
            filtered.append(row)
    return filtered


def _wire_scan_candidates(candidates: list, settings: dict) -> list:
    wired = [_candidate_wire(c, settings) for c in candidates]
    return _filter_openable_candidates(wired, settings)


def _run_meta_wire(meta: Optional[dict]) -> Optional[dict]:
    if not meta:
        return None
    return {
        "id": meta.get("id"),
        "started_at": meta.get("started_at"),
        "finished_at": meta.get("finished_at"),
        "candidate_count": meta.get("candidate_count"),
        "symbol_count": meta.get("symbol_count"),
        "trigger": meta.get("trigger"),
        "provider": meta.get("provider"),
        "diagnostics": meta.get("diagnostics"),
    }


@bp_scan.route("/scan/run", methods=["POST"])
def manual_scan():
    repo: Repo = current_app.config["REPO"]
    watchlist_syms = repo.list_enabled_watchlist_symbols()
    if not watchlist_syms:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "观察名单为空，请先填写并保存标的后再扫描",
                }
            ),
            400,
        )
    settings = repo.get_settings()
    risk_free = float(settings.get("risk_free_rate", 0.045))
    provider_name = settings.get("provider", "yfinance")

    run_id = repo.insert_scan_run(
        provider=provider_name,
        trigger="manual",
        symbol_count=len(watchlist_syms),
    )

    def _run():
        try:
            provider = YFinanceProvider()
            run_screener(
                repo,
                provider,
                trigger="manual",
                risk_free_rate=risk_free,
                run_id=run_id,
            )
        except Exception as exc:
            log.exception("manual_scan: background thread failed: %s", exc)
            try:
                repo.insert_event(
                    level="danger",
                    category="screener",
                    title=f"扫描线程异常：{exc}",
                    payload={"error": str(exc)},
                )
            except Exception:
                pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify(
        {
            "ok": True,
            "run_id": run_id,
            "message": "scan started in background",
        }
    )


@bp_scan.route("/scan/specific", methods=["POST"])
def scan_specific_put():
    """Look up one short-put contract by underlying, expiry, strike; same row shape as scan."""
    repo: Repo = current_app.config["REPO"]
    body = request.get_json(silent=True) or {}
    symbol = normalize_ticker_symbol(body.get("symbol", "") or "")
    if not symbol:
        return jsonify({"ok": False, "error": "请填写标的代码"}), 400
    exp_raw = body.get("expiration")
    exp_str = (str(exp_raw).strip()[:10] if exp_raw is not None else "") or ""
    try:
        exp_date = date.fromisoformat(exp_str)
    except ValueError:
        return jsonify({"ok": False, "error": "到期日格式无效（请使用 YYYY-MM-DD）"}), 400
    strike_raw = body.get("strike")
    try:
        strike_val = float(strike_raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "请输入有效行权价"}), 400
    settings = repo.get_settings()
    risk_free = float(settings.get("risk_free_rate", 0.045))
    provider_name = settings.get("provider", "yfinance")

    try:
        provider = YFinanceProvider()
        quote_plain = provider.get_quote(symbol)

        rv_data = settings.get("rv_by_symbol", {}).get(symbol)
        iv_rank = None
        if rv_data and isinstance(rv_data, list) and len(rv_data) > 5:
            current_rv = rv_data[-1] if rv_data else None
            if current_rv is not None:
                iv_rank = compute_iv_rank(current_rv, rv_data)

        quote = Quote(
            symbol=quote_plain.symbol,
            spot=quote_plain.spot,
            asof=quote_plain.asof,
            iv_rank=iv_rank,
        )

        exps = provider.get_expirations(symbol)
        if exps and exp_date not in exps:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "所选到期日不在该标的当前期权链中，请从行情终端核对日期格式",
                    }
                ),
                400,
            )

        contracts = provider.get_option_chain(
            symbol,
            exp_date,
            right="P",
            anchor_strike=strike_val,
            underlying_spot=quote.spot,
        )
        picked = _pick_put_near_strike(contracts, strike_val)
        if picked is None:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "未找到该行权价对应的卖_put 合约（或超出当前数据源返回范围）",
                    }
                ),
                404,
            )

        filled = fill_greeks(picked, quote.spot, risk_free)
        row = derive_csp_candidate_row(filled, quote, settings)
        if row is None:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "该合约暂无有效双边报价或缺少 Delta，无法计算与扫描一致的指标",
                        "diagnostics": {
                            "blockers": ["invalid_bid_ask_or_delta_missing"],
                        },
                    }
                ),
                400,
            )
        quality = evaluate_contract_quality(
            picked,
            filled,
            quote,
            settings,
            provider_name=provider.name,
            provider_realtime=provider.realtime,
            earnings_known=None,
        )
        row.update(
            {
                "quality_grade": quality.quality_grade,
                "quality_score": quality.quality_score,
                "quality_flags": quality.quality_flags,
                "quote_age_seconds": quality.quote_age_seconds,
                "greeks_source": quality.greeks_source,
                "iv_rank_source": quality.iv_rank_source,
            }
        )
        row["entry_signal"] = build_entry_signal(
            {**row, "right": "P", "status": "ACTIVE"},
            settings=settings,
            today=date.today(),
        )

        run_meta = {
            "id": None,
            "started_at": None,
            "finished_at": _now_utc(),
            "candidate_count": 1,
            "symbol_count": 1,
            "trigger": "specific",
            "provider": provider_name,
            "diagnostics": {
                "schema": "scan_diagnostics_v1",
                "totals": {
                    "symbols": 1,
                    "failed_symbols": 0,
                    "contracts_seen": 1,
                    "candidates": 1,
                    "quality_counts": {quality.quality_grade: 1},
                    "rejection_counts": {},
                },
                "symbols": {},
            },
        }
        return jsonify(
            {
                "schema": "scan_latest_v2",
                "candidates": [_candidate_wire(row)],
                "run": run_meta,
            }
        )
    except Exception as exc:
        log.exception("scan_specific_put failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 502


@bp_scan.route("/scan/run/<int:run_id>", methods=["GET"])
def scan_run_detail(run_id: int):
    """Return one scan run and its candidates (for polling a specific manual run)."""
    repo: Repo = current_app.config["REPO"]
    meta = repo.get_scan_run_meta(run_id)
    if meta is None:
        return jsonify({"ok": False, "error": "scan run not found"}), 404
    settings = repo.get_settings()
    candidates = repo.list_candidates(run_id, limit=50)
    return jsonify(
        {
            "schema": "scan_latest_v2",
            "candidates": _wire_scan_candidates(candidates, settings),
            "run": _run_meta_wire(meta),
        }
    )


@bp_scan.route("/scan/latest", methods=["GET"])
def latest_candidates():
    repo: Repo = current_app.config["REPO"]
    with repo._connect() as con:
        row = con.execute(
            "SELECT id "
            "FROM scan_runs WHERE finished_at IS NOT NULL "
            "ORDER BY finished_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            row = con.execute(
                "SELECT id "
                "FROM scan_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
    payload = {"schema": "scan_latest_v2", "candidates": [], "run": None}
    if not row:
        return jsonify(payload)
    run_id = row["id"]
    meta = repo.get_scan_run_meta(run_id)
    settings = repo.get_settings()
    candidates = repo.list_candidates(run_id, limit=50)
    return jsonify(
        {
            "schema": "scan_latest_v2",
            "candidates": _wire_scan_candidates(candidates, settings),
            "run": _run_meta_wire(meta),
        }
    )
