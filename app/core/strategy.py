from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from app.core.data_quality import ContractQuality, evaluate_contract_quality
from app.core.exit_signal import build_exit_signal
from app.core.types import OptionContract, Quote


def _normalize(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def derive_csp_candidate_row(
    c: OptionContract,
    quote: Quote,
    settings: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Build one short-put candidate dict (same shape as score_csp_candidates rows).

    Unlike score_csp_candidates, does not apply entry filters — used for targeted lookup.
    Returns None when quotes or Greeks are insufficient for CSP metrics.
    """
    if c.right != "P":
        return None
    spot = quote.spot
    today = date.today()
    dte = (c.expiration - today).days
    if dte <= 0:
        return None
    if c.bid is None or c.ask is None or c.bid <= 0 or c.ask <= 0:
        return None
    mid = (c.bid + c.ask) / 2.0
    if mid <= 0:
        return None
    if c.delta is None:
        return None
    abs_delta = abs(c.delta)
    spread_pct = (c.ask - c.bid) / mid
    margin_buffer = (spot - c.strike) / spot if spot > 0 else 0.0
    annualized_roi = (mid / c.strike) * (365.0 / dte) if dte > 0 else 0.0
    oi = c.open_interest or 0
    iv_rank = quote.iv_rank
    breakeven = c.strike - mid
    pop = 1.0 - abs_delta
    wts = settings.get("scoring_weights", {})
    score = (
        wts.get("annualized_roi", 0.35) * _normalize(annualized_roi, 0.15, 0.50)
        + wts.get("iv_rank", 0.25) * _normalize(iv_rank or 50.0, 30.0, 90.0)
        + wts.get("spread_pct", 0.15) * (1.0 - _normalize(spread_pct, 0.02, 0.15))
        + wts.get("margin_buffer", 0.15) * _normalize(margin_buffer, 0.05, 0.30)
        + wts.get("open_interest", 0.10) * _normalize(float(oi), 50.0, 5000.0)
    )
    return {
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
    result = score_csp_candidates_with_diagnostics(
        contracts,
        quote,
        settings,
        earnings_date=earnings_date,
    )
    return result["candidates"]


def _empty_diagnostics() -> Dict[str, Any]:
    return {
        "total_contracts": 0,
        "put_contracts": 0,
        "candidate_count": 0,
        "rejected_count": 0,
        "rejection_reasons": {},
        "quality_grades": {"A": 0, "B": 0, "C": 0, "unknown": 0},
        "rejected_contracts": [],
    }


def _quality_row_fields(quality: ContractQuality) -> Dict[str, Any]:
    return {
        "quality_grade": quality.quality_grade,
        "quality_score": quality.quality_score,
        "quality_flags": quality.quality_flags,
        "quote_age_seconds": quality.quote_age_seconds,
        "greeks_source": quality.greeks_source,
        "iv_rank_source": quality.iv_rank_source,
    }


def _count_rejection(diagnostics: Dict[str, Any], reason: str) -> None:
    reasons = diagnostics["rejection_reasons"]
    reasons[reason] = reasons.get(reason, 0) + 1


def score_csp_candidates_with_diagnostics(
    contracts: List[OptionContract],
    quote: Quote,
    settings: Dict[str, Any],
    earnings_date: Optional[date] = None,
    *,
    raw_contracts: Optional[List[OptionContract]] = None,
    provider_name: Optional[str] = None,
    provider_realtime: Optional[bool] = None,
    earnings_known: Optional[bool] = None,
    provider_error: bool = False,
) -> Dict[str, Any]:
    """
    Filter and score CSP candidates, with quality diagnostics for rejected rows.

    ``contracts`` are the filled contracts used by scoring.  ``raw_contracts``
    can be supplied in the same order to distinguish provider Greeks from
    Black-Scholes fallbacks.
    """
    diagnostics = _empty_diagnostics()
    diagnostics["total_contracts"] = len(contracts)
    raw_by_index = raw_contracts or contracts
    results: List[Dict[str, Any]] = []

    for idx, c in enumerate(contracts):
        if c.right != "P":
            continue
        diagnostics["put_contracts"] += 1
        raw = raw_by_index[idx] if idx < len(raw_by_index) else c
        quality = evaluate_contract_quality(
            raw,
            c,
            quote,
            settings,
            provider_name=provider_name,
            provider_realtime=provider_realtime,
            earnings_date=earnings_date,
            earnings_known=earnings_known,
            provider_error=provider_error,
        )
        diagnostics["quality_grades"][quality.quality_grade] = (
            diagnostics["quality_grades"].get(quality.quality_grade, 0) + 1
        )

        if quality.quality_grade == "C":
            diagnostics["rejected_count"] += 1
            for reason in quality.blocker_reasons or ["unknown"]:
                _count_rejection(diagnostics, reason)
            diagnostics["rejected_contracts"].append(
                {
                    "symbol": c.symbol,
                    "expiration": str(c.expiration),
                    "strike": c.strike,
                    "reasons": quality.blocker_reasons,
                    **_quality_row_fields(quality),
                }
            )
            continue

        row = derive_csp_candidate_row(c, quote, settings)
        if row is None:
            diagnostics["rejected_count"] += 1
            _count_rejection(diagnostics, "invalid_bid_ask")
            continue
        row.update(_quality_row_fields(quality))
        results.append(row)

    results = sorted(results, key=lambda x: x["score"], reverse=True)
    diagnostics["candidate_count"] = len(results)
    return {"candidates": results, "diagnostics": diagnostics}


def evaluate_exit_signals(
    position: Dict[str, Any],
    current_mid: float,
    current_spot: float,
    current_delta: Optional[float],
    settings: Dict[str, Any],
) -> List[str]:
    """Backward-compatible raw signal list derived from ``exit_signal_v1``."""
    open_premium = float(position.get("open_premium", 0) or 0)
    pnl_pct = 0.0
    if open_premium > 0 and current_mid >= 0:
        pnl_pct = 1.0 - (current_mid / open_premium)
    strike = float(position.get("strike", 0) or 0)
    margin_buffer = (
        (current_spot - strike) / current_spot
        if current_spot > 0 and strike > 0
        else None
    )
    mark = {
        "spot": current_spot,
        "option_mid": current_mid,
        "delta": current_delta,
        "margin_buffer": margin_buffer,
        "pnl_pct": pnl_pct,
    }
    return build_exit_signal(position, mark, settings).get("legacy_signals", [])
