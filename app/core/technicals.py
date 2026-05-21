"""Technical indicator pure functions for entry snapshot computation."""
from __future__ import annotations

import math
from typing import List, Optional


def compute_rsi(closes: List[float], period: int) -> Optional[float]:
    """
    Compute RSI(period) using simple-average method (oldest-first closes).
    Suitable for short periods (6, 12, 24). Returns None if insufficient data.
    """
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    relevant = deltas[-(period):]
    gains = [d for d in relevant if d > 0]
    losses = [-d for d in relevant if d < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def compute_rsi_wilder(closes: List[float], period: int = 14) -> Optional[float]:
    """
    Compute RSI(period) using Wilder's exponential smoothing (industry standard).

    RSI(14) is the canonical momentum oscillator used by tastytrade, ThinkorSwim,
    and most option-selling platforms. Requires at least 2*period+1 closes for a
    reliable warm-up; returns None if data is insufficient.

    Interpretation for Cash-Secured Short Put sellers:
      RSI < 30  — oversold: ideal entry (high IV, good premium, mean-reversion edge)
      RSI 30-50 — recovery zone: acceptable timing
      RSI 50-70 — neutral: rely on other filters
      RSI > 70  — overbought: caution, avoid chasing
    """
    needed = 2 * period + 1
    if len(closes) < needed:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    seed_gains = [d for d in deltas[:period] if d > 0]
    seed_losses = [-d for d in deltas[:period] if d < 0]
    avg_gain = sum(seed_gains) / period
    avg_loss = sum(seed_losses) / period

    alpha = 1.0 / period
    for d in deltas[period:]:
        g = d if d > 0 else 0.0
        l = -d if d < 0 else 0.0
        avg_gain = avg_gain * (1 - alpha) + g * alpha
        avg_loss = avg_loss * (1 - alpha) + l * alpha

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def compute_bb_lower_distance_pct(
    closes: List[float],
    window: int = 20,
    num_std: float = 2.0,
) -> Optional[float]:
    """
    Compute percentage distance of the last close above the lower Bollinger Band.
    Formula: (last_close - lower_band) / last_close * 100
    Returns None if insufficient data.
    Negative value means price is BELOW the lower band.
    """
    if len(closes) < window:
        return None
    recent = closes[-window:]
    sma = sum(recent) / window
    variance = sum((x - sma) ** 2 for x in recent) / window
    std = math.sqrt(variance)
    lower_band = sma - num_std * std
    last_close = closes[-1]
    if last_close == 0:
        return None
    return round((last_close - lower_band) / last_close * 100, 2)
