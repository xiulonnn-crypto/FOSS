from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from app.core.technicals import (
    compute_bb_lower_distance_pct,
    compute_bb_zscore,
    compute_hv,
    compute_macd_bias_pct,
    compute_rsi_wilder,
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
        )
    )


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
