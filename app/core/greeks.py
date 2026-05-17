from __future__ import annotations

import math
from datetime import date
from typing import Optional


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(
    spot: float,
    strike: float,
    rate: float,
    iv: float,
    t_years: float,
) -> tuple:
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return (float("nan"), float("nan"))
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t_years) / (
        iv * math.sqrt(t_years)
    )
    d2 = d1 - iv * math.sqrt(t_years)
    return d1, d2


def black_scholes_delta(
    spot: float,
    strike: float,
    rate: float,
    iv: float,
    t_years: float,
    right: str = "P",
) -> float:
    """Return BS Delta. Put: negative value."""
    d1, _ = _d1_d2(spot, strike, rate, iv, t_years)
    if math.isnan(d1):
        return float("nan")
    if right == "C":
        return _norm_cdf(d1)
    return _norm_cdf(d1) - 1.0  # Put


def black_scholes_gamma(
    spot: float,
    strike: float,
    rate: float,
    iv: float,
    t_years: float,
) -> float:
    d1, _ = _d1_d2(spot, strike, rate, iv, t_years)
    if math.isnan(d1):
        return float("nan")
    return _norm_pdf(d1) / (spot * iv * math.sqrt(t_years))


def black_scholes_theta(
    spot: float,
    strike: float,
    rate: float,
    iv: float,
    t_years: float,
    right: str = "P",
) -> float:
    """Return daily Theta (per-day decay, typically negative)."""
    d1, d2 = _d1_d2(spot, strike, rate, iv, t_years)
    if math.isnan(d1):
        return float("nan")
    term1 = -(spot * _norm_pdf(d1) * iv) / (2.0 * math.sqrt(t_years))
    if right == "C":
        term2 = -rate * strike * math.exp(-rate * t_years) * _norm_cdf(d2)
    else:
        term2 = rate * strike * math.exp(-rate * t_years) * _norm_cdf(-d2)
    return (term1 + term2) / 365.0


def black_scholes_vega(
    spot: float,
    strike: float,
    rate: float,
    iv: float,
    t_years: float,
) -> float:
    """Return Vega (sensitivity to 1-point IV move)."""
    d1, _ = _d1_d2(spot, strike, rate, iv, t_years)
    if math.isnan(d1):
        return float("nan")
    return spot * _norm_pdf(d1) * math.sqrt(t_years) * 0.01  # per 1% IV


def black_scholes_price(
    spot: float,
    strike: float,
    rate: float,
    iv: float,
    t_years: float,
    right: str = "P",
) -> float:
    """
    European Black-Scholes option value per share (not per contract).

    Uses the same log-normal assumptions as the Greeks helpers in this module.
    """
    iv = max(iv, 1e-12)
    t_years = max(t_years, 1e-12)
    d1, d2 = _d1_d2(spot, strike, rate, iv, t_years)
    if math.isnan(d1):
        return float("nan")
    disc = math.exp(-rate * t_years)
    if right == "C":
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_vol_black_scholes_put(
    spot: float,
    strike: float,
    rate: float,
    t_years: float,
    target_price: float,
    *,
    lo: float = 1e-5,
    hi: float = 5.0,
    tol: float = 1e-6,
    max_iter: int = 80,
) -> Optional[float]:
    """
    Black–Scholes implied volatility for a European put (per-share premium).

    Monotone in σ; bracket [lo, hi]. Returns None when target is outside BS range.
    """
    if spot <= 0 or strike <= 0 or t_years <= 1e-9 or target_price <= 0:
        return None
    intrinsic = max(0.0, strike - spot)
    if target_price + tol < intrinsic:
        return None

    def _pv(ivx: float) -> float:
        return black_scholes_price(spot, strike, rate, ivx, t_years, "P")

    p_lo = _pv(lo)
    p_hi = _pv(hi)
    if math.isnan(p_lo) or math.isnan(p_hi):
        return None

    if target_price <= p_lo + tol:
        return lo if target_price + tol >= intrinsic else None
    if target_price >= p_hi - tol:
        return hi
    if not (p_lo < target_price < p_hi):
        return None

    a_iv, b_iv = lo, hi
    for _ in range(max_iter):
        mid = (a_iv + b_iv) * 0.5
        pv = _pv(mid)
        if math.isnan(pv):
            return None
        if abs(pv - target_price) <= tol:
            return mid
        if pv < target_price:
            a_iv = mid
        else:
            b_iv = mid
    return (a_iv + b_iv) * 0.5


def fill_greeks(
    contract,  # OptionContract
    spot: float,
    rate: float = 0.045,
    valuation_date: Optional[date] = None,
) -> object:
    """Return a new OptionContract with any missing Greeks filled via BS.

    If ``valuation_date`` is set, DTE is computed from that day instead of today
    (for tests and mark-to-market that must align with a chosen spot time).
    """
    ref = valuation_date or date.today()
    dte = (contract.expiration - ref).days
    t_years = max(dte / 365.0, 1e-6)
    iv = contract.iv or 0.25  # fallback IV if missing

    delta = contract.delta
    gamma = contract.gamma
    theta = contract.theta
    vega = contract.vega

    if delta is None:
        delta = black_scholes_delta(spot, contract.strike, rate, iv, t_years, contract.right)
    if gamma is None:
        gamma = black_scholes_gamma(spot, contract.strike, rate, iv, t_years)
    if theta is None:
        theta = black_scholes_theta(spot, contract.strike, rate, iv, t_years, contract.right)
    if vega is None:
        vega = black_scholes_vega(spot, contract.strike, rate, iv, t_years)

    from dataclasses import replace
    return replace(contract, delta=delta, gamma=gamma, theta=theta, vega=vega)
