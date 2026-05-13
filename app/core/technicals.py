"""Technical indicator pure functions for entry snapshot computation."""
from __future__ import annotations

import math
from typing import List, Optional


def compute_rsi(closes: List[float], period: int) -> Optional[float]:
    """
    Compute RSI(period) from a list of closing prices (oldest first).
    Returns None if insufficient data (need period+1 closes minimum).
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
