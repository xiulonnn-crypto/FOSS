from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.greeks import fill_greeks
from app.core.strategy import score_csp_candidates, compute_iv_rank
from app.core.types import Quote
from app.data.provider_base import MarketDataProvider
from app.db.repo import Repo

log = logging.getLogger(__name__)

SNAPSHOTS_DIR = Path("data/snapshots")


def run_screener(
    repo: Repo,
    provider: MarketDataProvider,
    trigger: str = "scheduled",
    risk_free_rate: float = 0.045,
) -> None:
    """Scan watchlist, score candidates, persist results and emit events."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    settings = repo.get_settings()
    watchlist = [w["symbol"] for w in repo.list_watchlist() if w.get("enabled", 1)]

    if not watchlist:
        log.info("screener: watchlist empty, skipping")
        return

    run_id = repo.insert_scan_run(
        provider=provider.name,
        trigger=trigger,
        symbol_count=len(watchlist),
    )
    all_candidates = []
    snapshot_rows = []

    for symbol in watchlist:
        try:
            quote = provider.get_quote(symbol)
            expirations = provider.get_expirations(symbol)

            # IV rank via RV proxy
            rv_data = repo.get_settings().get("rv_by_symbol", {}).get(symbol)
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

            earnings_date = None
            try:
                earnings_date = provider.get_next_earnings(symbol)
            except Exception:
                pass

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
                    scored = score_csp_candidates(filled, quote_with_rank, settings, earnings_date)
                    for row in scored:
                        row["scan_run_id"] = run_id
                    all_candidates.extend(scored)
                except Exception as exc:
                    log.warning("screener: chain %s/%s error: %s", symbol, exp, exc)

        except Exception as exc:
            log.warning("screener: symbol %s error: %s", symbol, exc)

    # persist candidates
    if all_candidates:
        try:
            repo.insert_candidates(all_candidates)
        except Exception as exc:
            log.error("screener: insert_candidates error: %s", exc)

    # write snapshot
    snapshot_path = None
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        snap_file = SNAPSHOTS_DIR / f"screener_{ts}_{provider.name}.ndjson"
        with open(snap_file, "w") as f:
            for row in all_candidates:
                f.write(json.dumps(row, default=str) + "\n")
        snapshot_path = str(snap_file)
    except Exception as exc:
        log.warning("screener: snapshot write error: %s", exc)

    repo.finish_scan_run(run_id, len(all_candidates), snapshot_path)

    # emit event
    top_score = all_candidates[0]["score"] if all_candidates else None
    repo.insert_event(
        level="info",
        category="screener",
        title=f"Scan complete: {len(all_candidates)} candidates",
        payload={
            "scan_run_id": run_id,
            "candidate_count": len(all_candidates),
            "top_score": top_score,
        },
    )
    log.info("screener: run_id=%d candidates=%d", run_id, len(all_candidates))
