from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, List, Optional

from app.core.types import OptionContract, Quote, Settings


def _normalize(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def compute_iv_rank(current_rv: float, rv_history: List[float]) -> Optional[float]:
    """
    Compute IV Rank as percentile of current_rv in rv_history (RV proxy).
    Returns 0-100 or None if insufficient data.
    """
    if not rv_history or len(rv_history) < 5:
        return None
    lo = min(rv_history)
    hi = max(rv_history)
    if hi <= lo:
        return 50.0
    return round((current_rv - lo) / (hi - lo) * 100.0, 1)


def score_csp_candidates(
    contracts: List[OptionContract],
    quote: Quote,
    settings: Dict[str, Any],
    earnings_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """
    Filter and score Short Put candidates.
    Returns list of dicts with derived metrics + score, sorted score DESC.
    """
    today = date.today()
    flt = settings.get("filters", {})
    wts = settings.get("scoring_weights", {})

    delta_min = flt.get("delta_min", 0.10)
    delta_max = flt.get("delta_max", 0.20)
    dte_min = flt.get("dte_min", 30)
    dte_max = flt.get("dte_max", 45)
    roi_min = flt.get("annualized_roi_min", 0.20)
    spread_max = flt.get("spread_pct_max", 0.10)
    iv_rank_min = flt.get("iv_rank_min", 50)
    margin_min = flt.get("margin_buffer_min", 0.10)
    oi_min = flt.get("min_open_interest", 50)
    earnings_days = flt.get("exclude_earnings_within_days", 7)

    results = []
    spot = quote.spot

    for c in contracts:
        if c.right != "P":
            continue

        # --- DTE ---
        dte = (c.expiration - today).days
        if not (dte_min <= dte <= dte_max):
            continue

        # --- bid/ask ---
        if c.bid is None or c.ask is None or c.bid <= 0 or c.ask <= 0:
            continue
        mid = (c.bid + c.ask) / 2.0
        if mid <= 0:
            continue

        # --- spread ---
        spread_pct = (c.ask - c.bid) / mid
        if spread_pct > spread_max:
            continue

        # --- delta ---
        if c.delta is None:
            continue
        abs_delta = abs(c.delta)
        if not (delta_min <= abs_delta <= delta_max):
            continue

        # --- margin buffer ---
        margin_buffer = (spot - c.strike) / spot if spot > 0 else 0.0
        if margin_buffer < margin_min:
            continue

        # --- annualized ROI ---
        annualized_roi = (mid / c.strike) * (365.0 / dte) if dte > 0 else 0.0
        if annualized_roi < roi_min:
            continue

        # --- open interest ---
        oi = c.open_interest or 0
        if oi < oi_min:
            continue

        # --- IV rank ---
        iv_rank = quote.iv_rank
        if iv_rank is not None and iv_rank < iv_rank_min:
            continue

        # --- earnings exclusion ---
        if earnings_date is not None:
            days_to_earnings = (earnings_date - today).days
            if 0 <= days_to_earnings <= earnings_days:
                continue

        # --- derived metrics ---
        breakeven = c.strike - mid
        pop = 1.0 - abs_delta

        # --- score ---
        score = (
            wts.get("annualized_roi", 0.35) * _normalize(annualized_roi, 0.15, 0.50)
            + wts.get("iv_rank", 0.25) * _normalize(iv_rank or 50.0, 30.0, 90.0)
            + wts.get("spread_pct", 0.15) * (1.0 - _normalize(spread_pct, 0.02, 0.15))
            + wts.get("margin_buffer", 0.15) * _normalize(margin_buffer, 0.05, 0.30)
            + wts.get("open_interest", 0.10) * _normalize(float(oi), 50.0, 5000.0)
        )

        results.append(
            {
                "symbol": c.symbol,
                "expiration": str(c.expiration),
                "strike": c.strike,
                "bid": c.bid,
                "ask": c.ask,
                "mid": mid,
                "spot": spot,
                "iv": c.iv,
                "iv_rank": iv_rank,
                "delta": c.delta,
                "theta": c.theta,
                "vega": c.vega,
                "gamma": c.gamma,
                "dte": dte,
                "annualized_roi": round(annualized_roi, 4),
                "pop": round(pop, 4),
                "spread_pct": round(spread_pct, 4),
                "breakeven": round(breakeven, 4),
                "margin_buffer": round(margin_buffer, 4),
                "score": round(score, 4),
                "open_interest": oi,
            }
        )

    return sorted(results, key=lambda x: x["score"], reverse=True)


def evaluate_exit_signals(
    position: Dict[str, Any],
    current_mid: float,
    current_spot: float,
    current_delta: Optional[float],
    settings: Dict[str, Any],
) -> List[str]:
    """
    Return list of triggered signal IDs for an OPEN position.
    Signals: take_profit_50, take_profit_75, time_14d, time_7d,
             danger_3pct, delta_breach
    """
    exits = settings.get("exits", {})
    tp_pct = exits.get("take_profit_pct", 0.50)
    tp_strong = exits.get("take_profit_strong_pct", 0.75)
    time_warn = exits.get("time_warning_dte", 14)
    time_danger = exits.get("time_danger_dte", 7)
    dist_pct = exits.get("danger_distance_pct", 0.03)
    delta_thresh = exits.get("delta_breach_abs", 0.40)

    open_premium = float(position.get("open_premium", 0) or 0)
    expiration = position.get("expiration", "")
    strike = float(position.get("strike", 0) or 0)

    signals: List[str] = []

    # pnl_pct: fraction of max profit captured (1 - current/open)
    pnl_pct: float = 0.0
    if open_premium > 0 and current_mid >= 0:
        pnl_pct = 1.0 - (current_mid / open_premium)

    if pnl_pct >= tp_strong:
        signals.append("take_profit_75")
    elif pnl_pct >= tp_pct:
        signals.append("take_profit_50")

    # DTE
    try:
        exp_date = date.fromisoformat(expiration) if expiration else None
    except ValueError:
        exp_date = None
    if exp_date:
        dte = (exp_date - date.today()).days
        if dte <= time_danger:
            signals.append("time_7d")
        elif dte <= time_warn:
            signals.append("time_14d")

    # danger distance
    if strike > 0 and current_spot > 0:
        dist = (current_spot - strike) / strike
        if 0 <= dist <= dist_pct:
            signals.append("danger_3pct")
        elif dist < 0:
            signals.append("danger_3pct")

    # delta breach
    if current_delta is not None and abs(current_delta) >= delta_thresh:
        signals.append("delta_breach")

    return signals
