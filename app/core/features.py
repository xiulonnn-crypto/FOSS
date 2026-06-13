from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Union

from app.core.technicals import (
    compute_bb_lower_distance_pct,
    compute_bb_zscore,
    compute_hv,
    compute_macd_bias_pct,
    compute_rsi_wilder,
    compute_sma,
)


@dataclass(frozen=True)
class StateFeatures:
    rsi_14: Optional[float]
    macd_bias_pct: Optional[float]
    bb_zscore: Optional[float]
    bb_lower_distance_pct: Optional[float]
    hv30: Optional[float]
    iv30: Optional[float]
    vrp: Optional[float]
    skew: Optional[float]
    vix: Optional[float]
    iv_rank_true: Optional[float]
    regime: str
    trend_mode: str


def compute_state_features(
    closes: List[float],
    *,
    iv30: Optional[float] = None,
    skew: Optional[float] = None,
    vix: Optional[float] = None,
    rv_history: Optional[List[float]] = None,
    iv_history: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """Build the phase-one state feature payload for a symbol or candidate."""
    hv30 = compute_hv(closes, window=30)
    if hv30 is None and rv_history:
        hv30 = _last_float(rv_history)
    iv30_f = _to_float(iv30)
    vrp = round(iv30_f - hv30, 6) if iv30_f is not None and hv30 is not None else None

    ma_20 = compute_sma(closes, 20)
    ma_50 = compute_sma(closes, 50)
    last = closes[-1] if closes else None
    trend_mode = (
        "STRONG_TREND"
        if ma_20 is not None
        and ma_50 is not None
        and last is not None
        and last > ma_50
        and ma_20 > ma_50
        else "STANDARD"
    )

    return asdict(
        StateFeatures(
            rsi_14=compute_rsi_wilder(closes, period=14),
            macd_bias_pct=compute_macd_bias_pct(closes),
            bb_zscore=compute_bb_zscore(closes, window=20),
            bb_lower_distance_pct=compute_bb_lower_distance_pct(closes, window=20),
            hv30=hv30,
            iv30=iv30_f,
            vrp=vrp,
            skew=_to_float(skew),
            vix=_to_float(vix),
            iv_rank_true=_rank_min_max(iv30_f, iv_history),
            regime=detect_vix_regime(vix),
            trend_mode=trend_mode,
        )
    )


BB_SIGMA_STRONG = -2.0
BB_SIGMA_EXTREME = -3.0
RSI_OVERSOLD = 35.0
RSI_SUPPRESS = 60.0
DEFAULT_IV_RANK_MIN = 50.0

STRONG_TREND_BB_TIER3 = -1.5
STRONG_TREND_BB_TIER3_COMBO = -1.0
STRONG_TREND_RSI_TIER3 = 40.0
STRONG_TREND_BB_TIER2 = 0.0
STRONG_TREND_BB_TIER1_HIGH = 0.5

STANDARD_BB_TIER1_HIGH = -1.0


@dataclass(frozen=True)
class DipEvent:
    """Event-driven dip entry signal tier for CSP timing."""

    tier: int  # 0 = none, 1 = tactical, 2 = strong, 3 = extreme
    label: str = ""


def _threshold_float(value: Any, default: float) -> float:
    parsed = _to_float(value)
    return parsed if parsed is not None else default


def resolve_dip_thresholds(
    thresholds: Optional[Union[Mapping[str, Any], Dict[str, Any]]] = None,
) -> Dict[str, float]:
    """Merge ``settings.entry_signal`` / ``filters`` overrides with defaults."""
    src = dict(thresholds or {})
    entry = src.get("entry_signal")
    if isinstance(entry, dict):
        src = {**src, **entry}
    filters = src.get("filters")
    if isinstance(filters, dict):
        src = {**src, **{k: v for k, v in filters.items() if k not in src}}

    return {
        "bb_sigma_strong": _threshold_float(src.get("bb_sigma_strong"), BB_SIGMA_STRONG),
        "bb_sigma_extreme": _threshold_float(src.get("bb_sigma_extreme"), BB_SIGMA_EXTREME),
        "rsi_oversold": _threshold_float(src.get("rsi_oversold"), RSI_OVERSOLD),
        "rsi_suppress": _threshold_float(src.get("rsi_suppress"), RSI_SUPPRESS),
        "iv_rank_min": _threshold_float(src.get("iv_rank_min"), DEFAULT_IV_RANK_MIN),
    }


def detect_dip_event(
    bb_zscore: Optional[float],
    bb_distance_pct: Optional[float],
    rsi_14: Optional[float],
    iv_rank: Optional[float],
    thresholds: Optional[Union[Mapping[str, Any], Dict[str, Any]]] = None,
    *,
    trend_mode: str = "STANDARD",
) -> DipEvent:
    """Detect ideal CSP entry dip with dual-track Bollinger thresholds + high IVR."""
    thr = resolve_dip_thresholds(thresholds)
    iv_rank_f = _to_float(iv_rank)
    if iv_rank_f is None or iv_rank_f < thr["iv_rank_min"]:
        return DipEvent(tier=0, label="iv_rank_below_min")

    rsi_f = _to_float(rsi_14)
    if rsi_f is not None and rsi_f >= thr["rsi_suppress"]:
        return DipEvent(tier=0, label="rsi_not_weak")

    z = _to_float(bb_zscore)
    bb_dist = _to_float(bb_distance_pct)
    mode = str(trend_mode or "STANDARD").upper()
    if mode not in {"STRONG_TREND", "STANDARD"}:
        mode = "STANDARD"

    if mode == "STRONG_TREND":
        return _detect_dip_strong_trend(z, bb_dist, rsi_f)
    return _detect_dip_standard(z, bb_dist, rsi_f, thr)


def _detect_dip_standard(
    z: Optional[float],
    bb_dist: Optional[float],
    rsi_f: Optional[float],
    thr: Dict[str, float],
) -> DipEvent:
    sigma_strong = thr["bb_sigma_strong"]
    sigma_extreme = thr["bb_sigma_extreme"]
    rsi_oversold = thr["rsi_oversold"]

    if z is not None and z <= sigma_extreme:
        return DipEvent(tier=3, label="prime_extreme")
    if (
        z is not None
        and z <= sigma_strong
        and rsi_f is not None
        and rsi_f <= rsi_oversold
    ):
        return DipEvent(tier=3, label="prime_extreme")
    if z is not None and z <= sigma_strong:
        return DipEvent(tier=2, label="prime_strong")
    if z is None and bb_dist is not None and bb_dist < 0:
        return DipEvent(tier=2, label="prime_strong")
    if z is not None and sigma_strong < z <= STANDARD_BB_TIER1_HIGH:
        return DipEvent(tier=1, label="tactical_pullback")
    if z is None and bb_dist is not None and 0 <= bb_dist <= 5:
        return DipEvent(tier=1, label="tactical_pullback")
    return DipEvent(tier=0, label="channel_not_breached")


def _detect_dip_strong_trend(
    z: Optional[float],
    bb_dist: Optional[float],
    rsi_f: Optional[float],
) -> DipEvent:
    del bb_dist  # STRONG_TREND does not fall back to bb_distance when z is missing
    if z is None:
        return DipEvent(tier=0, label="channel_not_breached")
    if z <= STRONG_TREND_BB_TIER3:
        return DipEvent(tier=3, label="prime_extreme")
    if (
        z <= STRONG_TREND_BB_TIER3_COMBO
        and rsi_f is not None
        and rsi_f <= STRONG_TREND_RSI_TIER3
    ):
        return DipEvent(tier=3, label="prime_extreme")
    if z <= STRONG_TREND_BB_TIER2:
        return DipEvent(tier=2, label="prime_strong")
    if STRONG_TREND_BB_TIER3_COMBO < z <= STRONG_TREND_BB_TIER1_HIGH:
        return DipEvent(tier=1, label="tactical_near_middle")
    return DipEvent(tier=0, label="channel_not_breached")


def detect_vix_regime(vix: Optional[float]) -> str:
    vix_f = _to_float(vix)
    if vix_f is None:
        return "unknown"
    if vix_f >= 20.0:
        return "high_vol"
    if vix_f < 15.0:
        return "low_vol"
    return "neutral"


def _rank_min_max(value: Optional[float], history: Optional[List[float]]) -> Optional[float]:
    if value is None or not history:
        return None
    vals = [v for v in (_to_float(x) for x in history) if v is not None]
    if len(vals) < 2:
        return None
    lo = min(vals)
    hi = max(vals)
    if hi <= lo:
        return 50.0
    return round((value - lo) / (hi - lo) * 100.0, 1)


def _last_float(values: List[Any]) -> Optional[float]:
    for value in reversed(values):
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return None


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
