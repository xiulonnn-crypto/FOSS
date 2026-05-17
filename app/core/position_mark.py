from __future__ import annotations

import logging
import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.core.greeks import black_scholes_price, fill_greeks
from app.core.time_et import et_calendar_today, instant_to_et_iso
from app.core.types import OptionContract, Quote
from app.data.provider_base import MarketDataProvider

log = logging.getLogger(__name__)

# Prefer bid/ask mid when spread is sane; Yahoo IV→BS often diverges from broker marks.
_MAX_MARK_SPREAD_FRAC = 0.45


def _quality_chain_mid(contract: OptionContract, *, max_spread_frac: float = _MAX_MARK_SPREAD_FRAC) -> Optional[float]:
    """Return (bid+ask)/2 when quotes look actionable; else None."""
    bid, ask = contract.bid, contract.ask
    if bid is None or ask is None:
        return None
    if ask < bid or bid <= 0:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    spread_frac = (ask - bid) / mid
    if spread_frac > max_spread_frac:
        return None
    return mid


def mark_short_put_position(
    pos: Dict[str, Any],
    provider: MarketDataProvider,
    risk_free_rate: float = 0.045,
    *,
    prefetched_quote: Optional[Quote] = None,
    prefetched_chain: Optional[List[OptionContract]] = None,
) -> Dict[str, Any]:
    """
    Mark one OPEN short-put position: spot, per-share option mark, P&L metrics.

    ``option_mid`` prefers **bid/ask mid** from the matched chain row when the
    spread is reasonably tight (often closer to broker quotes than Yahoo IV→BS).
    Otherwise uses **European BS** at the position strike with chain IV.
    Last resort: raw mid if BS fails, else ``open_premium``.

    ``option_bs`` is the BS value whenever computed (for comparison). ``mark_basis``
    is ``mid`` | ``bs`` | ``mid_fallback``.
    """
    strike = float(pos.get("strike", 0) or 0)
    open_premium = float(pos.get("open_premium", 0) or 0)
    contracts = int(pos.get("contracts", 0) or 0)
    expiration_str = pos.get("expiration") or ""
    symbol = (pos.get("symbol") or "").upper()

    out: Dict[str, Any] = {}

    try:
        quote = prefetched_quote
        if quote is None:
            quote = provider.get_quote(symbol)
        spot = quote.spot
        out["spot"] = float(spot)
        asof = quote.asof
        out["spot_asof"] = instant_to_et_iso(asof) if isinstance(asof, datetime) else str(asof)
    except Exception as exc:
        log.warning("mark: quote(%s) failed: %s", symbol, exc)
        out["quote_error"] = str(exc)
        return out

    spot_f = float(spot)
    current_mid = open_premium
    current_delta: Optional[float] = None
    try:
        exp_date = date.fromisoformat(expiration_str)
        chain = prefetched_chain
        target = _prefetched_target(chain, strike) if chain is not None else None
        if target is None and (chain is None or len(chain) > 0):
            chain = provider.get_option_chain(
                symbol,
                exp_date,
                right="P",
                anchor_strike=strike,
                underlying_spot=spot_f,
            )
            target = min(chain, key=lambda c: abs(c.strike - strike), default=None)
        if target:
            ref = et_calendar_today()
            filled = fill_greeks(target, spot_f, risk_free_rate, valuation_date=ref)
            current_delta = filled.delta
            dte = (exp_date - ref).days
            t_years = max(dte / 365.0, 1e-6)
            iv = float(target.iv) if target.iv is not None and float(target.iv) > 1e-6 else 0.25
            theo = black_scholes_price(
                spot_f, strike, risk_free_rate, iv, t_years, "P"
            )
            bs_ok = not math.isnan(theo) and theo >= 0
            if bs_ok:
                out["option_bs"] = float(theo)
            mid_ok = _quality_chain_mid(target)
            if mid_ok is not None:
                current_mid = float(mid_ok)
                out["mark_basis"] = "mid"
            elif bs_ok:
                current_mid = float(theo)
                out["mark_basis"] = "bs"
            elif filled.mid is not None:
                current_mid = float(filled.mid)
                out["mark_basis"] = "mid_fallback"
    except Exception as exc:
        out["chain_error"] = str(exc)
        log.debug("mark: chain(%s) failed: %s", symbol, exc)

    pnl_pct = 0.0
    if open_premium > 0:
        pnl_pct = 1.0 - (current_mid / open_premium)

    margin_buffer = (spot_f - strike) / spot_f if spot_f > 0 and strike > 0 else 0.0

    out["option_mid"] = float(current_mid)
    if "mark_basis" not in out:
        out["mark_basis"] = "open_premium"
    out["delta"] = current_delta
    out["margin_buffer"] = round(margin_buffer, 4)
    out["pnl_pct"] = round(pnl_pct, 4)
    if contracts > 0:
        out["unrealized_pnl_usd"] = round((open_premium - current_mid) * 100.0 * contracts, 2)

    return out


def _prefetched_target(
    chain: Optional[List[OptionContract]],
    strike: float,
) -> Optional[OptionContract]:
    if not chain:
        return None
    target = min(chain, key=lambda c: abs(c.strike - strike), default=None)
    if target is None:
        return None
    # A chain prefetched without an anchor may omit far-from-spot strikes. In that
    # case, fall back to the anchored provider call instead of marking the wrong
    # strike from the broad cache.
    tolerance = max(0.01, abs(strike) * 0.0001)
    if abs(float(target.strike) - strike) > tolerance:
        return None
    return target
