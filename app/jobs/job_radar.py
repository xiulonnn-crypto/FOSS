from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from datetime import date, datetime, timezone
from typing import Optional

from app.core.greeks import fill_greeks
from app.core.strategy import evaluate_exit_signals
from app.data.provider_base import MarketDataProvider
from app.db.repo import Repo

log = logging.getLogger(__name__)

SERVER_NOTIFY_URL = "http://127.0.0.1:7000/api/internal/notify"


def _notify_server(event_id: int) -> None:
    """Best-effort HTTP POST to server; failure is non-fatal."""
    try:
        data = json.dumps({"id": event_id}).encode()
        req = urllib.request.Request(
            SERVER_NOTIFY_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            pass
    except Exception as exc:
        log.debug("radar: notify server failed (non-fatal): %s", exc)


def run_radar(
    repo: Repo,
    provider: MarketDataProvider,
    risk_free_rate: float = 0.045,
) -> None:
    """Evaluate all OPEN positions, write radar snapshots, emit exit signals."""
    settings = repo.get_settings()
    positions = repo.list_positions(state="OPEN")

    for pos in positions:
        symbol = pos["symbol"]
        position_id = pos["id"]
        strike = float(pos.get("strike", 0) or 0)
        open_premium = float(pos.get("open_premium", 0) or 0)
        expiration_str = pos.get("expiration", "")

        try:
            quote = provider.get_quote(symbol)
            spot = quote.spot
        except Exception as exc:
            log.warning("radar: quote(%s) failed: %s", symbol, exc)
            continue

        # Try to get current option mid
        current_mid = open_premium  # fallback: assume no change
        current_delta = None
        try:
            exp_date = date.fromisoformat(expiration_str)
            contracts = provider.get_option_chain(symbol, exp_date, right="P")
            # find closest strike
            target = min(contracts, key=lambda c: abs(c.strike - strike), default=None)
            if target:
                filled = fill_greeks(target, spot, risk_free_rate)
                if filled.mid is not None:
                    current_mid = filled.mid
                current_delta = filled.delta
        except Exception as exc:
            log.debug("radar: chain(%s) failed: %s", symbol, exc)

        # pnl_pct
        pnl_pct = 0.0
        if open_premium > 0:
            pnl_pct = 1.0 - (current_mid / open_premium)

        # margin buffer
        margin_buffer = (spot - strike) / spot if spot > 0 and strike > 0 else 0.0

        # evaluate signals
        signals = evaluate_exit_signals(pos, current_mid, spot, current_delta, settings)

        # write snapshot
        repo.insert_radar_snapshot(
            {
                "position_id": position_id,
                "taken_at": datetime.now(timezone.utc).isoformat(),
                "spot": spot,
                "current_mid": current_mid,
                "pnl_pct": round(pnl_pct, 4),
                "delta": current_delta,
                "margin_buffer": round(margin_buffer, 4),
                "signals": json.dumps(signals),
            }
        )

        # emit deduped events for new signals
        for sig in signals:
            if not repo.event_signal_exists(position_id, sig):
                level = "danger" if sig in ("time_7d", "danger_3pct", "delta_breach") else "warn"
                eid = repo.insert_event(
                    level=level,
                    category="radar",
                    title=f"{symbol} {sig.replace('_', ' ').upper()}",
                    payload={
                        "position_id": position_id,
                        "signal_type": sig,
                        "pnl_pct": round(pnl_pct, 4),
                        "spot": spot,
                        "strike": strike,
                    },
                )
                _notify_server(eid)

    log.info("radar: processed %d OPEN positions", len(positions))
