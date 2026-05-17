"""PnL-percent excursion helpers (radar snapshots + Massive EOD replay)."""


from __future__ import annotations

from typing import List, Optional, Tuple

_FLAT_EPS = 1e-9


def short_put_premium_pnl_pct(open_premium: float, option_mark: float) -> float:
    """Short premium canonical mark PnL%: ``1 − mark/open_premium``."""
    if open_premium <= 0:
        return 0.0
    return 1.0 - (float(option_mark) / float(open_premium))


def relative_mae_mfe_from_pnls_chronologic(pnls_sorted: List[float]) -> Tuple[Optional[float], Optional[float]]:
    """
    MAE/MFE versus the **first** observation in chronological order.

    Interpreted as excursion from the entry-period first daily mark snapshot (replay or EOD):

    - ``MAE = minₜ (pnl_t − pnl₀)`` (most adverse move vs opening observation)
    - ``MFE = maxₜ (pnl_t − pnl₀)``
    Returns ``(None, None)`` when there are fewer than two points or the path is numerically flat
    (``max − min`` below threshold), matching user expectation that 0%/0% is not meaningful data.
    """
    if len(pnls_sorted) < 2:
        return None, None
    lo = min(pnls_sorted)
    hi = max(pnls_sorted)
    if hi - lo <= _FLAT_EPS:
        return None, None
    baseline = pnls_sorted[0]
    ex = [float(v) - baseline for v in pnls_sorted]
    return min(ex), max(ex)
