from __future__ import annotations

import math
import pytest
from app.core.greeks import (
    black_scholes_delta,
    black_scholes_gamma,
    black_scholes_price,
    black_scholes_theta,
    black_scholes_vega,
    implied_vol_black_scholes_put,
)


def test_put_atm_delta_near_minus_half():
    d = black_scholes_delta(100.0, 100.0, 0.045, 0.25, 30 / 365.0, "P")
    assert -0.55 < d < -0.45, f"ATM Put delta should be near -0.5, got {d}"


def test_call_atm_delta_near_half():
    d = black_scholes_delta(100.0, 100.0, 0.045, 0.25, 30 / 365.0, "C")
    assert 0.45 < d < 0.55, f"ATM Call delta should be near +0.5, got {d}"


def test_put_call_delta_sum():
    spot, strike, rate, iv, t = 100.0, 100.0, 0.045, 0.25, 30 / 365.0
    dc = black_scholes_delta(spot, strike, rate, iv, t, "C")
    dp = black_scholes_delta(spot, strike, rate, iv, t, "P")
    # BS put-call delta parity: delta_call - delta_put = 1
    assert abs(dc - dp - 1.0) < 0.01, "Call delta - Put delta should equal ~1"


def test_gamma_positive():
    g = black_scholes_gamma(100.0, 100.0, 0.045, 0.25, 30 / 365.0)
    assert g > 0, "Gamma should be positive"


def test_theta_negative_for_put():
    th = black_scholes_theta(100.0, 100.0, 0.045, 0.25, 30 / 365.0, "P")
    assert th < 0, "Theta should be negative (time decay)"


def test_vega_positive():
    v = black_scholes_vega(100.0, 100.0, 0.045, 0.25, 30 / 365.0)
    assert v > 0, "Vega should be positive"


def test_put_call_parity_price():
    S, K, r, iv, T = 100.0, 100.0, 0.045, 0.25, 30 / 365.0
    c = black_scholes_price(S, K, r, iv, T, "C")
    p = black_scholes_price(S, K, r, iv, T, "P")
    assert abs((c - p) - (S - K * math.exp(-r * T))) < 1e-6


def test_put_price_positive():
    p = black_scholes_price(100.0, 100.0, 0.045, 0.25, 30 / 365.0, "P")
    assert p > 0


def test_zero_time_returns_nan():
    d = black_scholes_delta(100.0, 100.0, 0.045, 0.25, 0.0, "P")
    assert math.isnan(d), "Zero t_years should return nan"


def test_deep_otm_put_delta_small():
    # Strike 50, spot 100 → deep OTM Put, |delta| should be very small
    d = black_scholes_delta(100.0, 50.0, 0.045, 0.25, 30 / 365.0, "P")
    assert abs(d) < 0.01, f"Deep OTM Put delta should be near 0, got {d}"


def test_implied_iv_put_matches_forward_price():
    spot, strike, rate, iv, t = 102.5, 100.0, 0.045, 0.31, 40 / 365.0
    px = black_scholes_price(spot, strike, rate, iv, t, "P")
    iv_hat = implied_vol_black_scholes_put(spot, strike, rate, t, px)
    assert iv_hat is not None
    assert abs(iv_hat - iv) < 1e-5


def test_implied_iv_rejects_below_intrinsic():
    assert implied_vol_black_scholes_put(
        105.0, 140.0, 0.045, 20 / 365.0, 10.0,
    ) is None
