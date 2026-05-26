from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Optional

from app.core.data_quality import (
    make_scan_diagnostics,
    make_symbol_diagnostics,
    merge_counts,
    merge_symbol_into_scan,
)
from app.core.entry_signal import build_entry_signal
from app.core.features import compute_state_features
from app.core.greeks import fill_greeks
from app.core.option_pool import build_option_pool_row, evaluate_option_watch
from app.core.time_et import et_timestamp_for_filename
from app.core.strategy import compute_iv_rank, score_csp_candidates_with_diagnostics
from app.core.types import Quote
from app.data.provider_base import MarketDataProvider
from app.db.paths import snapshots_dir
from app.db.repo import Repo
from app.jobs.job_iv_history import run_iv_history

log = logging.getLogger(__name__)

SNAPSHOTS_DIR = snapshots_dir()


def run_option_pool_maintenance(repo: Repo) -> dict:
    """Lightweight expiry maintenance for pool/watch state."""
    pool = repo.mark_pool_missed_or_expired(today=date.today())
    expired_watches = repo.mark_option_watches_expired(today=date.today())
    return {"option_pool": pool, "option_watchlist_expired": expired_watches}


def _merge_strategy_diagnostics(symbol_diag: dict, strategy_diag: dict) -> None:
    symbol_diag["contracts_seen"] += int(strategy_diag.get("put_contracts", 0) or 0)
    symbol_diag["candidates"] += int(strategy_diag.get("candidate_count", 0) or 0)
    merge_counts(symbol_diag["quality_counts"], strategy_diag.get("quality_grades", {}))
    merge_counts(symbol_diag["rejection_counts"], strategy_diag.get("rejection_reasons", {}))


def _update_underlying_scan_summary(repo: Repo, symbol: str, symbol_diag: dict) -> None:
    try:
        repo.update_pool_underlying(
            symbol,
            {
                "last_scanned_at": datetime.now(timezone.utc).isoformat(),
                "last_candidate_count": int(symbol_diag.get("candidates", 0) or 0),
                "last_pool_summary": {
                    "status": symbol_diag.get("status"),
                    "expirations_seen": symbol_diag.get("expirations_seen", 0),
                    "contracts_seen": symbol_diag.get("contracts_seen", 0),
                    "candidates": symbol_diag.get("candidates", 0),
                    "quality_counts": symbol_diag.get("quality_counts", {}),
                    "rejection_counts": symbol_diag.get("rejection_counts", {}),
                    "errors": symbol_diag.get("errors", []),
                },
            },
        )
    except Exception as exc:
        log.debug("screener: underlying summary update skipped for %s: %s", symbol, exc)


def _build_symbol_state_features(
    repo: Repo,
    provider: MarketDataProvider,
    symbol: str,
    settings: dict,
) -> Optional[dict]:
    try:
        closes = provider.get_historical_closes(symbol, days=400)
    except Exception as exc:
        log.debug("screener: historical closes unavailable for %s: %s", symbol, exc)
        closes = []
    if not closes:
        return None
    iv_snapshot = repo.latest_market_iv_snapshot(symbol)
    rv_history = (settings.get("rv_by_symbol") or {}).get(symbol)
    iv_history = (settings.get("iv_by_symbol") or {}).get(symbol)
    return compute_state_features(
        closes,
        iv30=(iv_snapshot or {}).get("iv30") if iv_snapshot else None,
        skew=(iv_snapshot or {}).get("skew") if iv_snapshot else None,
        vix=(iv_snapshot or {}).get("vix") if iv_snapshot else None,
        rv_history=rv_history,
        iv_history=iv_history,
    )


def _sync_option_pool(
    repo: Repo,
    run_id: int,
    *,
    blocked_rows: list[dict],
    settings: Optional[dict] = None,
) -> dict:
    now = datetime.now(timezone.utc)
    saved_candidates = repo.list_candidates(run_id, limit=5000)
    pool_rows: list[dict] = []
    for row in saved_candidates:
        try:
            pool_rows.append(build_option_pool_row(row, scan_run_id=run_id, now=now))
        except Exception as exc:
            log.debug("screener: candidate pool row skipped: %s", exc)
    for row in blocked_rows:
        try:
            pool_rows.append(build_option_pool_row(row, scan_run_id=run_id, now=now))
        except Exception as exc:
            log.debug("screener: blocked pool row skipped: %s", exc)

    result = repo.upsert_option_pool_rows(pool_rows)
    seen_ids = result.get("upserted_ids", [])
    missed = repo.mark_pool_missed_or_expired(seen_ids, today=date.today())
    result["missed"] = missed
    signal_result = _generate_entry_signals(repo, seen_ids, settings or {})
    result["entry_signals"] = signal_result

    inserted = int(result.get("inserted", 0) or 0)
    high_score_rows = [
        row for row in pool_rows
        if row.get("status") != "BLOCKED" and (row.get("score") or 0) >= 0.75
    ]
    if inserted > 0 and high_score_rows:
        top = sorted(high_score_rows, key=lambda r: r.get("score") or 0, reverse=True)[0]
        repo.insert_event(
            level="info",
            category="option_pool",
            title=f"新高分合约入池：{top.get('symbol')} {top.get('expiration')} {top.get('strike')}P",
            payload={
                "scan_run_id": run_id,
                "score": top.get("score"),
                "symbol": top.get("symbol"),
                "expiration": top.get("expiration"),
                "strike": top.get("strike"),
            },
        )
    return result


def _generate_entry_signals(repo: Repo, option_pool_ids: list[int], settings: dict) -> dict:
    counts = {"generated": 0, "OPENABLE": 0, "WAIT": 0, "REJECT": 0, "EXPIRED": 0, "UNKNOWN": 0}
    for option_pool_id in dict.fromkeys(option_pool_ids):
        try:
            pool = repo.get_option_pool(int(option_pool_id))
            if not pool:
                continue
            signal = build_entry_signal(pool, settings=settings, today=date.today())
            repo.insert_entry_signal(signal)
            status = str(signal.get("status") or "UNKNOWN").upper()
            counts["generated"] += 1
            counts[status] = int(counts.get(status, 0) or 0) + 1
        except Exception as exc:
            log.debug("screener: entry signal skipped for pool_id=%s: %s", option_pool_id, exc)
    return counts


def _evaluate_option_watches(repo: Repo) -> dict:
    rows = repo.list_option_watches(status=["WATCHING", "READY"])
    counts = {"evaluated": 0, "ready": 0, "expired": 0, "invalid": 0}
    now = datetime.now(timezone.utc).isoformat()
    today = date.today()
    for watch in rows:
        pool = watch.get("option") or repo.get_option_pool(int(watch.get("option_pool_id") or 0))
        if not pool:
            continue
        signal = evaluate_option_watch(pool, watch, today)
        status = signal.get("status")
        previous_status = str(watch.get("status") or "").upper()
        previous_signal = watch.get("last_signal") or {}
        previous_event_key = previous_signal.get("event_status") or previous_signal.get("status")

        event_key = status
        if signal.get("reason") in {"pool_blocked", "pool_stale"}:
            event_key = signal.get("pool_status")
        signal_to_store = dict(signal)
        signal_to_store["event_status"] = event_key

        if status == "READY":
            counts["ready"] += 1
            if previous_event_key != event_key:
                repo.insert_event(
                    level="warn",
                    category="option_watch",
                    title=f"观察合约已达标：{pool.get('symbol')} {pool.get('expiration')} {pool.get('strike')}P",
                    payload={"watch_id": watch.get("id"), "option_pool_id": pool.get("id"), "signal": signal},
                )
        elif status == "EXPIRED":
            counts["expired"] += 1
            if previous_event_key != event_key:
                repo.insert_event(
                    level="info",
                    category="option_watch",
                    title=f"观察合约已过期：{pool.get('symbol')} {pool.get('expiration')} {pool.get('strike')}P",
                    payload={"watch_id": watch.get("id"), "option_pool_id": pool.get("id"), "signal": signal},
                )
        elif signal.get("reason") in {"pool_blocked", "pool_stale"}:
            counts["invalid"] += 1
            if previous_event_key != event_key:
                repo.insert_event(
                    level="info",
                    category="option_watch",
                    title=f"观察合约数据状态变化：{pool.get('symbol')} {pool.get('expiration')} {pool.get('strike')}P",
                    payload={"watch_id": watch.get("id"), "option_pool_id": pool.get("id"), "signal": signal},
                )

        repo.persist_option_watch_evaluation(
            int(watch["id"]),
            status=status if status and status != previous_status else None,
            last_signal=signal_to_store,
            evaluated_at=now,
        )
        counts["evaluated"] += 1
    return counts


def run_screener(
    repo: Repo,
    provider: MarketDataProvider,
    trigger: str = "scheduled",
    risk_free_rate: float = 0.045,
    run_id: Optional[int] = None,
) -> None:
    """Scan watchlist, score candidates, persist results and emit events.

    If ``run_id`` is set, the scan_run row must already exist (e.g. manual scan);
    otherwise a new row is inserted at start.
    """
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    watchlist = repo.list_enabled_watchlist_symbols()
    diagnostics = make_scan_diagnostics(len(watchlist))

    if not watchlist:
        log.info("screener: watchlist empty, skipping")
        if run_id is not None:
            try:
                repo.finish_scan_run(run_id, 0, None, diagnostics=diagnostics)
            except Exception as exc:
                log.error("screener: finish_scan_run (empty watchlist) error: %s", exc)
        return

    if run_id is None:
        run_id = repo.insert_scan_run(
            provider=provider.name,
            trigger=trigger,
            symbol_count=len(watchlist),
        )
    all_candidates = []
    blocked_pool_rows = []
    snapshot_rows = []
    snapshot_path = None
    fatal_exc = None
    actual_saved = 0
    settings = repo.get_settings()

    try:
        # Refresh RV series used as IV-rank proxy before scoring (writes settings.rv_by_symbol).
        run_iv_history(repo, provider)
        settings = repo.get_settings()

        for symbol in watchlist:
            symbol_diag = make_symbol_diagnostics()
            try:
                quote = provider.get_quote(symbol)
                expirations = provider.get_expirations(symbol)
                symbol_diag["expirations_seen"] = len(expirations)

                # IV rank via RV proxy
                rv_data = settings.get("rv_by_symbol", {}).get(symbol)
                iv_rank = None
                if rv_data and isinstance(rv_data, list) and len(rv_data) > 5:
                    current_rv = rv_data[-1] if rv_data else None
                    if current_rv is not None:
                        iv_rank = compute_iv_rank(current_rv, rv_data)
                quote_with_rank = Quote(
                    symbol=quote.symbol,
                    spot=quote.spot,
                    asof=quote.asof,
                    iv_rank=iv_rank,
                )
                symbol_state_features = _build_symbol_state_features(repo, provider, symbol, settings)

                earnings_date = None
                earnings_known = None
                try:
                    earnings_date = provider.get_next_earnings(symbol)
                    earnings_known = earnings_date is not None
                except Exception:
                    earnings_known = False

                for exp in expirations:
                    try:
                        contracts = provider.get_option_chain(symbol, exp, right="P")
                        # fill missing greeks via BS
                        filled = []
                        for c in contracts:
                            try:
                                filled.append(fill_greeks(c, quote.spot, risk_free_rate))
                            except Exception:
                                filled.append(c)
                        snapshot_rows.extend([
                            {"symbol": symbol, "exp": str(exp), **(vars(c) if hasattr(c, "__dict__") else {})}
                            for c in filled
                        ])
                        scored_result = score_csp_candidates_with_diagnostics(
                            filled,
                            quote_with_rank,
                            settings,
                            earnings_date,
                            raw_contracts=contracts,
                            provider_name=provider.name,
                            provider_realtime=provider.realtime,
                            earnings_known=earnings_known,
                        )
                        scored = scored_result["candidates"]
                        _merge_strategy_diagnostics(
                            symbol_diag, scored_result.get("diagnostics", {})
                        )
                        blocked_pool_rows.extend(
                            scored_result.get("diagnostics", {}).get("rejected_contracts", [])
                        )
                        for row in scored:
                            row["scan_run_id"] = run_id
                            if symbol_state_features is not None:
                                features = dict(symbol_state_features)
                                if features.get("iv30") is None and row.get("iv") is not None:
                                    features = compute_state_features(
                                        provider.get_historical_closes(symbol, days=400),
                                        iv30=row.get("iv"),
                                        skew=features.get("skew"),
                                        vix=features.get("vix"),
                                        rv_history=rv_data,
                                        iv_history=(settings.get("iv_by_symbol") or {}).get(symbol),
                                    )
                                row["state_features"] = features
                        all_candidates.extend(scored)
                    except Exception as exc:
                        symbol_diag["errors"].append(
                            {"stage": "chain", "expiration": str(exp), "error": str(exc)}
                        )
                        log.warning("screener: chain %s/%s error: %s", symbol, exp, exc)

            except Exception as exc:
                symbol_diag["status"] = "error"
                symbol_diag["errors"].append({"stage": "symbol", "error": str(exc)})
                log.warning("screener: symbol %s error: %s", symbol, exc)
            finally:
                merge_symbol_into_scan(diagnostics, symbol, symbol_diag)
                _update_underlying_scan_summary(repo, symbol, symbol_diag)

        # persist candidates
        if all_candidates:
            try:
                repo.insert_candidates(all_candidates)
                for candidate in repo.list_candidates(run_id, limit=5000):
                    features = candidate.get("state_features")
                    if features:
                        repo.insert_feature_snapshot(
                            "candidate",
                            int(candidate["id"]),
                            features,
                            as_of=datetime.now(timezone.utc).isoformat(),
                        )
            except Exception as exc:
                log.exception(
                    "screener: run_id=%d insert_candidates failed: %s", run_id, exc
                )

        # write snapshot
        try:
            ts = et_timestamp_for_filename()
            snap_file = SNAPSHOTS_DIR / f"screener_{ts}_{provider.name}.ndjson"
            with open(snap_file, "w") as f:
                for row in all_candidates:
                    f.write(json.dumps(row, default=str) + "\n")
            snapshot_path = str(snap_file)
        except Exception as exc:
            log.warning("screener: snapshot write error: %s", exc)

    except Exception as exc:
        fatal_exc = exc
        log.exception("screener: run_id=%d fatal error: %s", run_id, exc)
    finally:
        try:
            actual_saved = repo.count_candidates(run_id)
            diagnostics["totals"]["candidates"] = actual_saved
            pool_result = _sync_option_pool(
                repo,
                run_id,
                blocked_rows=blocked_pool_rows,
                settings=settings,
            )
            watch_result = _evaluate_option_watches(repo)
            signal_counts = pool_result.get("entry_signals", {})
            diagnostics["totals"]["option_pool_inserted"] = int(pool_result.get("inserted", 0) or 0)
            diagnostics["totals"]["option_pool_updated"] = int(pool_result.get("updated", 0) or 0)
            diagnostics["totals"]["option_pool_seen"] = len(pool_result.get("upserted_ids", []) or [])
            diagnostics["totals"]["option_pool_missed"] = pool_result.get("missed", {})
            diagnostics["totals"]["entry_signal_counts"] = signal_counts
            diagnostics["totals"]["openable_count"] = int(signal_counts.get("OPENABLE", 0) or 0)
            diagnostics["totals"]["wait_count"] = int(signal_counts.get("WAIT", 0) or 0)
            diagnostics["totals"]["reject_count"] = int(signal_counts.get("REJECT", 0) or 0)
            diagnostics["totals"]["option_watch_evaluated"] = int(watch_result.get("evaluated", 0) or 0)
            diagnostics["totals"]["option_watch_ready"] = int(watch_result.get("ready", 0) or 0)
            repo.finish_scan_run(
                run_id,
                actual_saved,
                snapshot_path,
                diagnostics=diagnostics,
            )
        except Exception as exc:
            log.error("screener: finish_scan_run error: %s", exc)

    if fatal_exc is not None:
        try:
            repo.insert_event(
                level="danger",
                category="screener",
                title=f"扫描失败：{fatal_exc}",
                payload={
                    "scan_run_id": run_id,
                    "error": str(fatal_exc),
                    "diagnostics": diagnostics,
                },
            )
        except Exception:
            pass
        log.info("screener: run_id=%d aborted", run_id)
        return

    expected_count = len(all_candidates)
    if expected_count > 0 and actual_saved == 0:
        try:
            repo.insert_event(
                level="danger",
                category="screener",
                title=f"扫描结果未能写入数据库（期望 {expected_count} 条）",
                payload={
                    "scan_run_id": run_id,
                    "expected_rows": expected_count,
                    "persisted_rows": actual_saved,
                    "quality_counts": diagnostics.get("totals", {}).get("quality_counts"),
                    "failed_symbols": diagnostics.get("totals", {}).get("failed_symbols"),
                },
            )
        except Exception:
            pass
        log.warning(
            "screener: run_id=%d persist failed memory=%d db=%d",
            run_id,
            expected_count,
            actual_saved,
        )
        return

    if expected_count > 0 and actual_saved != expected_count:
        log.warning(
            "screener: run_id=%d row count mismatch memory=%d db=%d",
            run_id,
            expected_count,
            actual_saved,
        )

    top_score = all_candidates[0]["score"] if actual_saved > 0 else None
    repo.insert_event(
        level="info",
        category="screener",
        title=f"扫描完成：已写入 {actual_saved} 条候选",
        payload={
            "scan_run_id": run_id,
            "candidate_count": actual_saved,
            "top_score": top_score,
            "trigger": trigger,
            "quality_counts": diagnostics.get("totals", {}).get("quality_counts"),
            "failed_symbols": diagnostics.get("totals", {}).get("failed_symbols"),
        },
    )
    log.info(
        "screener: run_id=%d candidates persisted=%d (scored=%d)",
        run_id,
        actual_saved,
        expected_count,
    )
