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


def compute_bb_zscore(closes: List[float], window: int = 20) -> Optional[float]:
    """Return last close's Z-score versus its Bollinger midline window."""
    if len(closes) < window:
        return None
    recent = closes[-window:]
    sma = sum(recent) / window
    variance = sum((x - sma) ** 2 for x in recent) / window
    std = math.sqrt(variance)
    if std == 0:
        return None
    return round((recent[-1] - sma) / std, 6)


def compute_hv(closes: List[float], window: int = 30) -> Optional[float]:
    """Annualized historical volatility from the latest `window` simple returns."""
    if len(closes) < window + 1:
        return None
    recent = closes[-(window + 1):]
    returns: List[float] = []
    for i in range(1, len(recent)):
        prev = recent[i - 1]
        cur = recent[i]
        if prev <= 0 or cur <= 0:
            return None
        returns.append((cur / prev) - 1.0)
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return round(math.sqrt(variance) * math.sqrt(252.0), 6)


def compute_macd_bias_pct(
    closes: List[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Optional[float]:
    """Return (MACD - signal line) as a percentage of the latest close."""
    if len(closes) < slow + signal:
        return None
    last_close = closes[-1]
    if last_close == 0:
        return None
    fast_ema = _ema_series(closes, fast)
    slow_ema = _ema_series(closes, slow)
    if len(fast_ema) != len(closes) or len(slow_ema) != len(closes):
        return None
    macd = [f - s for f, s in zip(fast_ema, slow_ema)]
    signal_line = _ema_series(macd, signal)
    if not signal_line:
        return None
    hist = macd[-1] - signal_line[-1]
    return round(hist / last_close * 100.0, 6)


def _ema_series(values: List[float], period: int) -> List[float]:
    if not values or period <= 0:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [float(values[0])]
    for value in values[1:]:
        out.append((float(value) * alpha) + (out[-1] * (1.0 - alpha)))
    return out
