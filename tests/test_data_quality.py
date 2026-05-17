from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

from app.core.data_quality import evaluate_contract_quality
from app.core.strategy import score_csp_candidates_with_diagnostics
from app.core.types import OptionContract, Quote


def _contract(
    *,
    bid=1.55,
    ask=1.65,
    delta=-0.15,
    open_interest=500,
    volume=100,
    dte_offset=35,
) -> OptionContract:
    return OptionContract(
        symbol="AAPL",
        expiration=date.today() + timedelta(days=dte_offset),
        strike=150.0,
        right="P",
        bid=bid,
        ask=ask,
        last=1.6,
        iv=0.28,
        delta=delta,
        theta=-0.03,
        vega=0.05,
        gamma=0.01,
        open_interest=open_interest,
        volume=volume,
        quote_age_seconds=15,
    )


SETTINGS = {
    "filters": {
        "delta_min": 0.10,
        "delta_max": 0.20,
        "dte_min": 30,
        "dte_max": 45,
        "annualized_roi_min": 0.05,
        "spread_pct_max": 0.10,
        "iv_rank_min": 50,
        "margin_buffer_min": 0.05,
        "min_open_interest": 50,
        "exclude_earnings_within_days": 7,
    },
    "scoring_weights": {
        "annualized_roi": 0.35,
        "iv_rank": 0.25,
        "spread_pct": 0.15,
        "margin_buffer": 0.15,
        "open_interest": 0.10,
    },
}

QUOTE = Quote(symbol="AAPL", spot=175.0, asof=datetime.utcnow(), iv_rank=65.0)


def test_provider_greeks_and_rv_proxy_grade_a():
    c = _contract()
    quality = evaluate_contract_quality(
        c,
        c,
        QUOTE,
        SETTINGS,
        provider_name="yfinance",
        provider_realtime=True,
        earnings_known=True,
    )
    assert quality.quality_grade == "A"
    assert quality.quality_score == 100
    assert quality.greeks_source == "provider"
    assert quality.iv_rank_source == "rv_proxy"
    assert quality.quality_flags == ["iv_rank_proxy"]


def test_bs_fallback_greeks_grade_b_with_warning():
    raw = _contract(delta=None)
    filled = replace(raw, delta=-0.15, theta=-0.03, vega=0.05, gamma=0.01)
    result = score_csp_candidates_with_diagnostics(
        [filled],
        QUOTE,
        SETTINGS,
        raw_contracts=[raw],
        provider_realtime=True,
        earnings_known=True,
    )
    assert result["diagnostics"]["candidate_count"] == 1
    row = result["candidates"][0]
    assert row["quality_grade"] == "B"
    assert row["greeks_source"] == "bs_fallback"
    assert "greeks_bs_fallback" in row["quality_flags"]


def test_missing_bid_ask_is_c_blocker_and_excluded():
    c = _contract(bid=None, ask=None)
    result = score_csp_candidates_with_diagnostics([c], QUOTE, SETTINGS)
    assert result["candidates"] == []
    assert result["diagnostics"]["candidate_count"] == 0
    assert result["diagnostics"]["rejected_count"] == 1
    assert result["diagnostics"]["rejection_reasons"]["invalid_bid_ask"] == 1
    assert result["diagnostics"]["rejected_contracts"][0]["quality_grade"] == "C"


def test_wide_spread_is_c_blocker_and_excluded():
    c = _contract(bid=1.0, ask=1.5)
    result = score_csp_candidates_with_diagnostics([c], QUOTE, SETTINGS)
    assert result["candidates"] == []
    assert result["diagnostics"]["rejection_reasons"]["wide_spread"] == 1


def test_missing_delta_is_c_blocker_and_excluded():
    c = _contract(delta=None)
    result = score_csp_candidates_with_diagnostics([c], QUOTE, SETTINGS)
    assert result["candidates"] == []
    assert result["diagnostics"]["rejection_reasons"]["delta_missing"] == 1


def test_diagnostics_count_candidates_and_multiple_rejection_reasons():
    good = _contract()
    no_quote = _contract(bid=None, ask=None)
    low_oi = _contract(open_interest=10)
    result = score_csp_candidates_with_diagnostics(
        [good, no_quote, low_oi],
        QUOTE,
        SETTINGS,
    )
    diagnostics = result["diagnostics"]
    assert diagnostics["total_contracts"] == 3
    assert diagnostics["put_contracts"] == 3
    assert diagnostics["candidate_count"] == 1
    assert diagnostics["rejected_count"] == 2
    assert diagnostics["rejection_reasons"]["invalid_bid_ask"] == 1
    assert diagnostics["rejection_reasons"]["oi_below_min"] == 1
