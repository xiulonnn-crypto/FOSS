from __future__ import annotations

import math

from app.core.features import compute_state_features
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
