"""Build Massive/Polygon-style OSI option tickers: ``O:SYMYYMMDDC/Pssssssss``."""

from __future__ import annotations

from datetime import date


def format_osi_option_ticker(
    underlying: str,
    expiration: date,
    right: str,
    strike: float,
) -> str:
    """
    Strike is encoded as ``round(strike * 1000)`` zero-padded to 8 digits
    (e.g. SPY 720 put -> ``...P00720000``).
    """
    sym = (underlying or "").upper().strip()
    if not sym:
        raise ValueError("empty underlying")
    r = (right or "P").upper().strip()
    cp = "C" if r.startswith("C") else "P"
    ymd = expiration.strftime("%y%m%d")
    strike_int = int(round(float(strike) * 1000))
    if strike_int < 0 or strike_int > 99_999_999:
        raise ValueError("strike out of range for OSI encoding")
    strike_part = f"{strike_int:08d}"
    return f"O:{sym}{ymd}{cp}{strike_part}"
