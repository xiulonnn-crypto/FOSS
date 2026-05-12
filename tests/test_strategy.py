from __future__ import annotations

from datetime import date, timedelta
from typing import List

import pytest

from app.core.strategy import evaluate_exit_signals, score_csp_candidates
from app.core.types import OptionContract, Quote


def _make_contract(
    symbol="AAPL",
    dte_offset=35,
    strike=150.0,
    bid=1.55,
    ask=1.65,
    delta=-0.15,
    open_interest=500,
    iv=0.28,
    right="P",
) -> OptionContract:
    return OptionContract(
        symbol=symbol,
        expiration=date.today() + timedelta(days=dte_offset),
        strike=strike,
        right=right,
        bid=bid,
        ask=ask,
        last=1.6,
        iv=iv,
        delta=delta,
        theta=-0.03,
        vega=0.05,
        gamma=0.01,
        open_interest=open_interest,
        volume=100,
    )


SETTINGS = {
    "filters": {
        "delta_min": 0.10,
        "delta_max": 0.20,
        "dte_min": 30,
        "dte_max": 45,
        "annualized_roi_min": 0.05,
        "spread_pct_max": 0.10,
        "iv_rank_min": 0,  # disable IV rank filter for unit tests
        "margin_buffer_min": 0.05,
        "min_open_interest": 50,
        "exclude_earnings_within_days": 7,
    },
    "exits": {
        "take_profit_pct": 0.50,
        "take_profit_strong_pct": 0.75,
        "time_warning_dte": 14,
        "time_danger_dte": 7,
        "danger_distance_pct": 0.03,
        "delta_breach_abs": 0.40,
    },
    "scoring_weights": {
        "annualized_roi": 0.35,
        "iv_rank": 0.25,
        "spread_pct": 0.15,
        "margin_buffer": 0.15,
        "open_interest": 0.10,
    },
}

QUOTE = Quote(symbol="AAPL", spot=175.0, asof=__import__("datetime").datetime.utcnow(), iv_rank=65.0)


# ------------------------------------------------------------------
# score_csp_candidates — hard filters
# ------------------------------------------------------------------

def test_passes_all_filters():
    c = _make_contract()
    results = score_csp_candidates([c], QUOTE, SETTINGS)
    assert len(results) == 1


def test_filtered_by_spread_too_wide():
    c = _make_contract(bid=1.0, ask=1.5)  # spread 0.5/1.25 = 40%
    results = score_csp_candidates([c], QUOTE, SETTINGS)
    assert len(results) == 0


def test_filtered_by_dte_too_short():
    c = _make_contract(dte_offset=10)  # DTE < 30
    results = score_csp_candidates([c], QUOTE, SETTINGS)
    assert len(results) == 0


def test_filtered_by_delta_too_large():
    c = _make_contract(delta=-0.40)  # |delta| > 0.20
    results = score_csp_candidates([c], QUOTE, SETTINGS)
    assert len(results) == 0


def test_filtered_by_oi_too_low():
    c = _make_contract(open_interest=10)
    results = score_csp_candidates([c], QUOTE, SETTINGS)
    assert len(results) == 0


def test_sorted_by_score_desc():
    c1 = _make_contract(symbol="AAPL", bid=2.0, ask=2.2, delta=-0.15, strike=150.0)
    c2 = _make_contract(symbol="TSLA", bid=1.0, ask=1.1, delta=-0.12, strike=150.0)
    results = score_csp_candidates([c1, c2], QUOTE, SETTINGS)
    # Both pass — c1 has higher mid → higher ROI → higher score
    assert len(results) == 2
    assert results[0]["score"] >= results[1]["score"]


# ------------------------------------------------------------------
# evaluate_exit_signals
# ------------------------------------------------------------------

def _open_pos(dte_offset=35, strike=150.0, open_premium=2.0):
    return {
        "symbol": "AAPL",
        "expiration": str(date.today() + timedelta(days=dte_offset)),
        "strike": strike,
        "contracts": 1,
        "open_premium": open_premium,
        "state": "OPEN",
    }


def test_take_profit_50():
    signals = evaluate_exit_signals(
        _open_pos(), current_mid=0.9, current_spot=175.0,
        current_delta=-0.10, settings=SETTINGS
    )
    assert "take_profit_50" in signals


def test_take_profit_75():
    signals = evaluate_exit_signals(
        _open_pos(), current_mid=0.4, current_spot=175.0,
        current_delta=-0.08, settings=SETTINGS
    )
    assert "take_profit_75" in signals


def test_time_14d_warning():
    signals = evaluate_exit_signals(
        _open_pos(dte_offset=12), current_mid=1.5, current_spot=175.0,
        current_delta=-0.15, settings=SETTINGS
    )
    assert "time_14d" in signals


def test_time_7d_danger():
    signals = evaluate_exit_signals(
        _open_pos(dte_offset=5), current_mid=1.5, current_spot=175.0,
        current_delta=-0.15, settings=SETTINGS
    )
    assert "time_7d" in signals


def test_danger_3pct():
    # spot=152, strike=150 → distance = (152-150)/150 = 1.33% < 3%
    signals = evaluate_exit_signals(
        _open_pos(strike=150.0), current_mid=1.5, current_spot=152.0,
        current_delta=-0.35, settings=SETTINGS
    )
    assert "danger_3pct" in signals


def test_delta_breach():
    signals = evaluate_exit_signals(
        _open_pos(), current_mid=1.5, current_spot=175.0,
        current_delta=-0.45, settings=SETTINGS
    )
    assert "delta_breach" in signals


def test_no_signals_healthy_position():
    signals = evaluate_exit_signals(
        _open_pos(), current_mid=1.8, current_spot=175.0,
        current_delta=-0.15, settings=SETTINGS
    )
    assert signals == []
