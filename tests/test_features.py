from __future__ import annotations

import math

from app.core.features import compute_state_features, detect_dip_event
from app.core.technicals import compute_bb_zscore, compute_hv, compute_macd_bias_pct


def test_compute_hv_uses_annualized_rolling_returns():
    closes = [100.0, 101.0, 99.0, 102.0, 103.0, 101.0]

    hv = compute_hv(closes, window=5)

    returns = [
        (closes[i] / closes[i - 1]) - 1.0
        for i in range(1, len(closes))
    ]
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    expected = math.sqrt(variance) * math.sqrt(252.0)
    assert hv == round(expected, 6)


def test_technical_indicators_return_none_when_history_is_too_short():
    closes = [100.0, 101.0, 102.0]

    assert compute_hv(closes, window=30) is None
    assert compute_bb_zscore(closes, window=20) is None
    assert compute_macd_bias_pct(closes) is None


def test_compute_state_features_combines_momentum_volatility_and_regime():
    closes = [100.0 + math.sin(i / 3.0) + i * 0.15 for i in range(80)]
    rv_history = [0.18, 0.2, 0.22, 0.24, 0.26]
    iv_history = [0.2, 0.25, 0.3, 0.35, 0.4]

    features = compute_state_features(
        closes,
        iv30=0.35,
        skew=0.06,
        vix=22.0,
        rv_history=rv_history,
        iv_history=iv_history,
    )

    assert features["rsi_14"] is not None
    assert features["macd_bias_pct"] is not None
    assert features["bb_zscore"] is not None
    assert features["hv30"] is not None
    assert features["iv30"] == 0.35
    assert features["vrp"] == round(0.35 - features["hv30"], 6)
    assert features["skew"] == 0.06
    assert features["vix"] == 22.0
    assert features["iv_rank_true"] == 75.0
    assert features["regime"] == "high_vol"


def test_detect_dip_event_tier2_on_minus2_sigma_and_high_iv():
    dip = detect_dip_event(-2.1, None, 40.0, 55.0, {"filters": {"iv_rank_min": 50}})
    assert dip.tier == 2
    assert dip.label == "prime_strong"


def test_detect_dip_event_tier3_on_minus3_sigma():
    dip = detect_dip_event(-3.2, None, 40.0, 60.0)
    assert dip.tier == 3
    assert dip.label == "prime_extreme"


def test_detect_dip_event_tier0_when_iv_rank_low():
    dip = detect_dip_event(-2.5, None, 30.0, 40.0, {"filters": {"iv_rank_min": 50}})
    assert dip.tier == 0
    assert dip.label == "iv_rank_below_min"


def test_detect_dip_event_suppressed_when_rsi_not_weak():
    dip = detect_dip_event(-2.5, None, 65.0, 70.0)
    assert dip.tier == 0
    assert dip.label == "rsi_not_weak"


def test_detect_dip_event_upgrades_strong_to_extreme_with_deep_oversold():
    dip = detect_dip_event(-2.1, None, 30.0, 55.0)
    assert dip.tier == 3
    assert dip.label == "prime_extreme"


def test_detect_dip_event_fallback_to_bb_distance_when_zscore_missing():
    dip = detect_dip_event(None, -1.5, 40.0, 55.0)
    assert dip.tier == 2


def test_detect_dip_event_strong_trend_tier2_on_zero_zscore():
    dip = detect_dip_event(0.0, None, 45.0, 55.0, trend_mode="STRONG_TREND")
    assert dip.tier == 2
    assert dip.label == "prime_strong"


def test_detect_dip_event_strong_trend_tier3_on_minus1_5():
    dip = detect_dip_event(-1.6, None, 45.0, 55.0, trend_mode="STRONG_TREND")
    assert dip.tier == 3
    assert dip.label == "prime_extreme"


def test_detect_dip_event_strong_trend_tier3_combo_rsi40():
    dip = detect_dip_event(-1.1, None, 38.0, 55.0, trend_mode="STRONG_TREND")
    assert dip.tier == 3
    assert dip.label == "prime_extreme"


def test_detect_dip_event_strong_trend_tier1_near_middle():
    dip = detect_dip_event(0.3, None, 45.0, 55.0, trend_mode="STRONG_TREND")
    assert dip.tier == 1
    assert dip.label == "tactical_near_middle"


def test_detect_dip_event_strong_trend_suppressed_by_rsi_60():
    dip = detect_dip_event(-1.0, None, 62.0, 55.0, trend_mode="STRONG_TREND")
    assert dip.tier == 0
    assert dip.label == "rsi_not_weak"


def test_detect_dip_event_standard_tier1_on_minus1_5_sigma():
    dip = detect_dip_event(-1.5, None, 45.0, 55.0, trend_mode="STANDARD")
    assert dip.tier == 1
    assert dip.label == "tactical_pullback"


def test_compute_state_features_includes_trend_mode():
    closes = [100.0 + i * 0.5 for i in range(70)]
    features = compute_state_features(closes)
    assert features["trend_mode"] == "STRONG_TREND"
