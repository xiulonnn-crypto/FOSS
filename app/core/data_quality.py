from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.core.types import OptionContract, Quote


INFO_FLAGS = frozenset({"provider_delayed", "iv_rank_proxy", "earnings_unknown"})
WARN_FLAGS = frozenset(
    {
        "greeks_bs_fallback",
        "iv_rank_missing",
        "volume_missing",
        "wide_but_allowed_spread",
    }
)

WARN_DEDUCTIONS = {
    "greeks_bs_fallback": 15,
    "iv_rank_missing": 10,
    "volume_missing": 5,
    "wide_but_allowed_spread": 10,
}

BLOCKER_REASONS = frozenset(
    {
        "invalid_bid_ask",
        "wide_spread",
        "delta_missing",
        "oi_below_min",
        "dte_out_of_range",
        "roi_below_min",
        "margin_buffer_low",
        "earnings_within_window",
        "iv_rank_below_min",
        "provider_error",
    }
)


@dataclass(frozen=True)
class ContractQuality:
    grade: str
    score: int
    flags: List[str] = field(default_factory=list)
    quote_age_seconds: Optional[int] = None
    greeks_source: str = "missing"
    iv_rank_source: str = "missing"
    blocker_reasons: List[str] = field(default_factory=list)

    @property
    def quality_grade(self) -> str:
        return self.grade

    @property
    def quality_score(self) -> int:
        return self.score

    @property
    def quality_flags(self) -> List[str]:
        return list(self.flags)

    def as_flat_fields(self) -> Dict[str, Any]:
        return {
            "quality_grade": self.grade,
            "quality_score": self.score,
            "quality_flags": list(self.flags),
            "quote_age_seconds": self.quote_age_seconds,
            "greeks_source": self.greeks_source,
            "iv_rank_source": self.iv_rank_source,
        }

    def as_data_quality(self) -> Dict[str, Any]:
        return {
            "grade": self.grade,
            "score": self.score,
            "flags": list(self.flags),
            "quote_age_seconds": self.quote_age_seconds,
            "greeks_source": self.greeks_source,
            "iv_rank_source": self.iv_rank_source,
        }


def quality_counts_template() -> Dict[str, int]:
    return {"A": 0, "B": 0, "C": 0, "unknown": 0}


def make_scan_diagnostics(symbol_count: int = 0) -> Dict[str, Any]:
    return {
        "schema": "scan_diagnostics_v1",
        "totals": {
            "symbols": symbol_count,
            "failed_symbols": 0,
            "contracts_seen": 0,
            "candidates": 0,
            "quality_counts": quality_counts_template(),
            "rejection_counts": {},
        },
        "symbols": {},
    }


def make_symbol_diagnostics() -> Dict[str, Any]:
    return {
        "status": "ok",
        "expirations_seen": 0,
        "contracts_seen": 0,
        "candidates": 0,
        "quality_counts": quality_counts_template(),
        "rejection_counts": {},
        "errors": [],
    }


def increment_count(counts: Dict[str, int], key: str, amount: int = 1) -> None:
    counts[key] = int(counts.get(key, 0) or 0) + amount


def merge_counts(dst: Dict[str, int], src: Dict[str, int]) -> None:
    for key, value in src.items():
        increment_count(dst, key, int(value or 0))


def merge_symbol_into_scan(scan: Dict[str, Any], symbol: str, sym_diag: Dict[str, Any]) -> None:
    scan.setdefault("symbols", {})[symbol] = sym_diag
    totals = scan.setdefault("totals", {})
    totals["contracts_seen"] = int(totals.get("contracts_seen", 0) or 0) + int(
        sym_diag.get("contracts_seen", 0) or 0
    )
    totals["candidates"] = int(totals.get("candidates", 0) or 0) + int(
        sym_diag.get("candidates", 0) or 0
    )
    if sym_diag.get("status") == "error":
        totals["failed_symbols"] = int(totals.get("failed_symbols", 0) or 0) + 1
    merge_counts(totals.setdefault("quality_counts", quality_counts_template()), sym_diag.get("quality_counts", {}))
    merge_counts(totals.setdefault("rejection_counts", {}), sym_diag.get("rejection_counts", {}))


def detect_greeks_source(raw: OptionContract, filled: OptionContract) -> str:
    if filled.delta is None:
        return "missing"
    raw_values = (raw.delta, raw.gamma, raw.theta, raw.vega)
    filled_values = (filled.delta, filled.gamma, filled.theta, filled.vega)
    if all(v is not None for v in raw_values):
        return "provider"
    if any(rv is None and fv is not None for rv, fv in zip(raw_values, filled_values)):
        return "bs_fallback"
    return "provider"


def assess_contract_quality(
    raw_contract: OptionContract,
    filled_contract: OptionContract,
    quote: Quote,
    settings: Dict[str, Any],
    *,
    provider_name: str = "unknown",
    provider_realtime: bool = False,
    earnings_known: Optional[bool] = None,
    blockers: Optional[Iterable[str]] = None,
) -> ContractQuality:
    blocker_list = [b for b in (blockers or []) if b]
    flags: List[str] = []

    quote_age = filled_contract.quote_age_seconds
    if quote_age is None:
        quote_age = raw_contract.quote_age_seconds
    if not provider_realtime:
        flags.append("provider_delayed")

    greeks_source = detect_greeks_source(raw_contract, filled_contract)
    if greeks_source == "bs_fallback":
        flags.append("greeks_bs_fallback")

    iv_rank_source = "rv_proxy" if quote.iv_rank is not None else "missing"
    if iv_rank_source == "rv_proxy":
        flags.append("iv_rank_proxy")
    else:
        flags.append("iv_rank_missing")

    if earnings_known is False:
        flags.append("earnings_unknown")

    if filled_contract.volume is None or int(filled_contract.volume or 0) <= 0:
        flags.append("volume_missing")

    spread_pct = _spread_pct(filled_contract)
    spread_max = settings.get("filters", {}).get("spread_pct_max", 0.10)
    if spread_pct is not None and spread_pct <= spread_max and spread_pct >= spread_max * 0.8:
        flags.append("wide_but_allowed_spread")

    flags = _dedupe(flags)
    if blocker_list:
        return ContractQuality(
            grade="C",
            score=0,
            flags=_dedupe([*flags, *blocker_list]),
            quote_age_seconds=quote_age,
            greeks_source=greeks_source,
            iv_rank_source=iv_rank_source,
            blocker_reasons=_dedupe(blocker_list),
        )

    score = 100
    for flag in flags:
        if flag in WARN_FLAGS:
            score -= WARN_DEDUCTIONS.get(flag, 0)
    score = max(0, min(100, int(score)))
    warn_present = any(flag in WARN_FLAGS for flag in flags)
    if score >= 90 and not warn_present:
        grade = "A"
    elif score >= 60:
        grade = "B"
    else:
        grade = "C"
    return ContractQuality(
        grade=grade,
        score=score,
        flags=flags,
        quote_age_seconds=quote_age,
        greeks_source=greeks_source,
        iv_rank_source=iv_rank_source,
        blocker_reasons=[],
    )


def evaluate_contract_quality(
    raw_contract: OptionContract,
    filled_contract: OptionContract,
    quote: Quote,
    settings: Dict[str, Any],
    *,
    provider_name: Optional[str] = None,
    provider_realtime: Optional[bool] = None,
    earnings_date: Optional[date] = None,
    earnings_known: Optional[bool] = None,
    provider_error: bool = False,
    valuation_date: Optional[date] = None,
) -> ContractQuality:
    del provider_name

    flt = settings.get("filters", {}) if isinstance(settings, dict) else {}
    today = valuation_date or date.today()
    blockers: List[str] = []

    if provider_error:
        blockers.append("provider_error")

    dte = (filled_contract.expiration - today).days
    if not (flt.get("dte_min", 30) <= dte <= flt.get("dte_max", 45)):
        blockers.append("dte_out_of_range")

    bid = filled_contract.bid
    ask = filled_contract.ask
    mid: Optional[float] = None
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        blockers.append("invalid_bid_ask")
    else:
        mid = (bid + ask) / 2.0
        if mid <= 0:
            blockers.append("invalid_bid_ask")
        else:
            spread_pct = (ask - bid) / mid
            if spread_pct > flt.get("spread_pct_max", 0.10):
                blockers.append("wide_spread")

    if filled_contract.delta is None:
        blockers.append("delta_missing")
    else:
        abs_delta = abs(filled_contract.delta)
        if not (flt.get("delta_min", 0.10) <= abs_delta <= flt.get("delta_max", 0.20)):
            blockers.append("delta_missing")

    margin_buffer = (quote.spot - filled_contract.strike) / quote.spot if quote.spot > 0 else 0.0
    if margin_buffer < flt.get("margin_buffer_min", 0.10):
        blockers.append("margin_buffer_low")

    if mid is not None and dte > 0:
        annualized_roi = (mid / filled_contract.strike) * (365.0 / dte)
        if annualized_roi < flt.get("annualized_roi_min", 0.20):
            blockers.append("roi_below_min")

    if (filled_contract.open_interest or 0) < flt.get("min_open_interest", 50):
        blockers.append("oi_below_min")

    if quote.iv_rank is not None and quote.iv_rank < flt.get("iv_rank_min", 50):
        blockers.append("iv_rank_below_min")

    if earnings_date is not None:
        days_to_earnings = (earnings_date - today).days
        if 0 <= days_to_earnings <= flt.get("exclude_earnings_within_days", 7):
            blockers.append("earnings_within_window")

    return assess_contract_quality(
        raw_contract,
        filled_contract,
        quote,
        settings,
        provider_realtime=bool(provider_realtime),
        earnings_known=earnings_known,
        blockers=_dedupe(blockers),
    )


def infer_quality_from_candidate_snapshot(
    row: Dict[str, Any],
    settings: Dict[str, Any],
    *,
    provider_realtime: bool = False,
) -> Optional[ContractQuality]:
    """Infer a conservative quality grade for legacy candidate snapshots.

    Older databases may have candidate metrics but no quality columns.  The
    stored candidate row has enough data to avoid a blanket "unknown" display,
    but not enough provenance to claim a full provider-grade assessment, so an
    inferred A is capped at B and marked with ``snapshot_inferred``.
    """
    symbol = str(row.get("symbol") or "").strip().upper()
    expiration_raw = str(row.get("expiration") or "").strip()[:10]
    try:
        expiration = date.fromisoformat(expiration_raw)
    except ValueError:
        return None

    strike = _to_optional_float(row.get("strike"))
    spot = _to_optional_float(row.get("spot"))
    if not symbol or strike is None or spot is None:
        return None

    dte = _to_optional_int(row.get("dte"))
    if dte is None:
        dte = (expiration - date.today()).days

    contract = OptionContract(
        symbol=symbol,
        expiration=expiration,
        strike=strike,
        right="P",
        bid=_to_optional_float(row.get("bid")),
        ask=_to_optional_float(row.get("ask")),
        last=None,
        iv=_to_optional_float(row.get("iv")),
        delta=_to_optional_float(row.get("delta")),
        theta=_to_optional_float(row.get("theta")),
        vega=_to_optional_float(row.get("vega")),
        gamma=_to_optional_float(row.get("gamma")),
        open_interest=_to_optional_int(row.get("open_interest")),
        volume=_to_optional_int(row.get("volume")),
        quote_age_seconds=_to_optional_int(row.get("quote_age_seconds")),
    )
    quote = Quote(
        symbol=symbol,
        spot=spot,
        asof=datetime.now(timezone.utc),
        iv_rank=_to_optional_float(row.get("iv_rank")),
    )
    flt = settings.get("filters", {}) if isinstance(settings, dict) else {}
    blockers: List[str] = []
    bid = contract.bid
    ask = contract.ask
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        blockers.append("invalid_bid_ask")

    spread_pct = _to_optional_float(row.get("spread_pct"))
    if spread_pct is None:
        spread_pct = _spread_pct(contract)
    if spread_pct is not None and spread_pct > flt.get("spread_pct_max", 0.10):
        blockers.append("wide_spread")

    if contract.delta is None:
        blockers.append("delta_missing")
    else:
        abs_delta = abs(contract.delta)
        if not (flt.get("delta_min", 0.10) <= abs_delta <= flt.get("delta_max", 0.20)):
            blockers.append("delta_missing")

    if not (flt.get("dte_min", 30) <= dte <= flt.get("dte_max", 45)):
        blockers.append("dte_out_of_range")

    margin_buffer = _to_optional_float(row.get("margin_buffer"))
    if margin_buffer is None and spot > 0:
        margin_buffer = (spot - strike) / spot
    if margin_buffer is not None and margin_buffer < flt.get("margin_buffer_min", 0.10):
        blockers.append("margin_buffer_low")

    annualized_roi = _to_optional_float(row.get("annualized_roi"))
    if annualized_roi is None and bid is not None and ask is not None and dte > 0:
        mid = (bid + ask) / 2.0
        annualized_roi = (mid / strike) * (365.0 / dte)
    if annualized_roi is not None and annualized_roi < flt.get("annualized_roi_min", 0.20):
        blockers.append("roi_below_min")

    if (contract.open_interest or 0) < flt.get("min_open_interest", 50):
        blockers.append("oi_below_min")

    if quote.iv_rank is not None and quote.iv_rank < flt.get("iv_rank_min", 50):
        blockers.append("iv_rank_below_min")

    quality = assess_contract_quality(
        contract,
        contract,
        quote,
        settings,
        provider_realtime=provider_realtime,
        earnings_known=None,
        blockers=_dedupe(blockers),
    )

    grade = quality.grade
    score = quality.score
    if grade == "A":
        grade = "B"
        score = min(score, 85)
    return ContractQuality(
        grade=grade,
        score=score,
        flags=_dedupe([*quality.flags, "snapshot_inferred"]),
        quote_age_seconds=quality.quote_age_seconds,
        greeks_source=quality.greeks_source,
        iv_rank_source=quality.iv_rank_source,
        blocker_reasons=quality.blocker_reasons,
    )


def _spread_pct(c: OptionContract) -> Optional[float]:
    if c.bid is None or c.ask is None or c.bid <= 0 or c.ask <= 0:
        return None
    mid = (c.bid + c.ask) / 2.0
    if mid <= 0:
        return None
    return (c.ask - c.bid) / mid


def _to_optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _dedupe(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
