from __future__ import annotations

from typing import Literal


def settle_short_put(
    spot_close: float,
    strike: float,
) -> Literal["expired_otm", "assigned"]:
    """
    Determine outcome of an expiring Short Put.

    If spot_close > strike the put expires OTM: seller keeps full premium.
    If spot_close <= strike the put expires ITM: seller is assigned (must buy stock).
    """
    if spot_close > strike:
        return "expired_otm"
    return "assigned"


def calc_realized_pnl(
    open_premium: float,
    close_premium: float,
    contracts: int,
    fee_per_contract: float = 1.0,
) -> float:
    """
    Realized P&L for a Short Put in USD.

    For early close:  (open_premium - close_premium) * 100 * contracts - fees
    For expiry (pass close_premium=0 for full credit):
        open_premium * 100 * contracts - fees
    """
    gross = (open_premium - close_premium) * 100 * contracts
    fees = fee_per_contract * contracts
    return round(gross - fees, 2)
