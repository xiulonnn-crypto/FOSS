from __future__ import annotations

import math
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


def fill_greeks(
    contract,  # OptionContract
    spot: float,
    rate: float = 0.045,
) -> object:
    """Return a new OptionContract with any missing Greeks filled via BS."""
    from datetime import date as _date

    dte = (contract.expiration - _date.today()).days
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
