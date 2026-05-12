from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from app.core.settlement import calc_realized_pnl, settle_short_put
from app.data.provider_base import MarketDataProvider
from app.db.repo import Repo

log = logging.getLogger(__name__)


def run_settlement(
    repo: Repo,
    provider: MarketDataProvider,
) -> None:
    """Settle all OPEN positions whose expiration date <= today."""
    settings = repo.get_settings()
    fee_per_contract = settings.get("fees", {}).get("usd_per_contract", 1.0)
    today = date.today()

    open_positions = repo.list_positions(state="OPEN")
    to_settle = [p for p in open_positions if date.fromisoformat(p["expiration"]) <= today]

    if not to_settle:
        log.info("settlement: nothing to settle today")
        return

    for pos in to_settle:
        symbol = pos["symbol"]
        strike = float(pos.get("strike", 0) or 0)
        contracts = int(pos.get("contracts", 1) or 1)
        open_premium = float(pos.get("open_premium", 0) or 0)
        exp_date = date.fromisoformat(pos["expiration"])

        spot_close = None
        try:
            spot_close = provider.get_historical_close(symbol, exp_date)
        except Exception as exc:
            log.warning("settlement: get_historical_close(%s, %s) failed: %s", symbol, exp_date, exc)

        if spot_close is None:
            log.warning("settlement: no close price for %s on %s, skipping", symbol, exp_date)
            continue

        outcome = settle_short_put(spot_close, strike)

        if outcome == "expired_otm":
            close_premium = 0.0
            state = "EXPIRED_OTM"
            close_reason = "expired_otm"
        else:
            # assigned: intrinsic value
            close_premium = max(0.0, strike - spot_close)
            state = "ASSIGNED"
            close_reason = "assigned"

        pnl = calc_realized_pnl(open_premium, close_premium, contracts, fee_per_contract)
        repo.close_position(pos["id"], state, close_premium, close_reason, pnl)

        level = "info" if outcome == "expired_otm" else "warn"
        eid = repo.insert_event(
            level=level,
            category="settlement",
            title=f"{symbol} {state}: spot={spot_close:.2f} strike={strike:.2f}",
            payload={
                "position_id": pos["id"],
                "outcome": outcome,
                "spot_close": spot_close,
                "strike": strike,
                "pnl": pnl,
            },
        )
        log.info("settlement: %s %s pnl=%.2f event=%d", symbol, outcome, pnl, eid)

    log.info("settlement: settled %d positions", len(to_settle))
