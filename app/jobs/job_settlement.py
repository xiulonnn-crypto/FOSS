from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone

from app.core.radar_snapshot import append_radar_snapshot_from_mark
from app.core.settlement import calc_realized_pnl, settle_short_put
from app.core.time_et import APP_TZ
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
            fee_legs = 1
        else:
            # assigned: intrinsic value
            close_premium = max(0.0, strike - spot_close)
            state = "ASSIGNED"
            close_reason = "assigned"
            fee_legs = 2

        pnl = calc_realized_pnl(
            open_premium, close_premium, contracts, fee_per_contract, fee_legs=fee_legs
        )

        close_dt = datetime.combine(exp_date, time(16, 0), tzinfo=APP_TZ)
        close_ts = close_dt.astimezone(timezone.utc).isoformat()

        open_pf = float(open_premium)
        cp_f = float(close_premium)
        mb = (
            (spot_close - strike) / spot_close
            if spot_close > 0 and strike > 0
            else 0.0
        )
        pnl_pct_mark = (1.0 - (cp_f / open_pf)) if open_pf > 0 else 0.0
        mark: Optional[dict] = {
            "spot": float(spot_close),
            "option_mid": cp_f,
            "pnl_pct": float(pnl_pct_mark),
            "margin_buffer": float(mb),
            "delta": None,
        }

        try:
            append_radar_snapshot_from_mark(
                repo, pos["id"], close_ts, mark, signals=None
            )
        except Exception as exc:
            log.warning(
                "settlement: radar snapshot failed (non-fatal) position_id=%s: %s",
                pos["id"],
                exc,
            )

        repo.close_position(
            pos["id"], state, close_premium, close_reason, pnl, close_at=close_ts
        )

        try:
            repo.save_position_close_snapshot(
                pos["id"],
                {
                    "schema": "position_close_snapshot_v1",
                    "closed_at": close_ts,
                    "close_premium": close_premium,
                    "selected_close_reason": close_reason,
                    "close_notes": None,
                    "realized_pnl": pnl,
                    "exit_signal_id": None,
                    "exit_signal": None,
                    "mark": mark,
                },
                close_signal_id=None,
            )
        except Exception as exc:
            log.warning(
                "settlement: close_snapshot save failed (non-fatal) position_id=%s: %s",
                pos["id"],
                exc,
            )

        level = "info" if outcome == "expired_otm" else "warn"
        if outcome == "expired_otm":
            title = (
                f"{symbol} 到期结算：虚值未指派 · 收盘 {spot_close:.2f} · 行权 {strike:.2f}"
            )
        else:
            title = f"{symbol} 到期结算：已指派 · 收盘 {spot_close:.2f} · 行权 {strike:.2f}"
        eid = repo.insert_event(
            level=level,
            category="settlement",
            title=title,
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
