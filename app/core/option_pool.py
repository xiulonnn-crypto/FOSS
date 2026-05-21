from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional


OPTION_POOL_STATUSES = frozenset({"NEW", "ACTIVE", "STALE", "EXPIRED", "BLOCKED"})
OPTION_WATCH_STATUSES = frozenset({"WATCHING", "READY", "IGNORED", "OPENED", "EXPIRED"})
ACTIONABLE_POOL_STATUSES = frozenset({"NEW", "ACTIVE"})
TERMINAL_WATCH_STATUSES = frozenset({"IGNORED", "OPENED", "EXPIRED"})
STALE_MISSED_SCAN_COUNT = 2

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


def build_option_pool_row(
    candidate_or_blocked_row: Dict[str, Any],
    scan_run_id: Optional[int],
    now: datetime,
) -> Dict[str, Any]:
    """Normalize a scanner row into the option_pool row contract."""
    symbol = _normalize_symbol(candidate_or_blocked_row.get("symbol"))
    expiration = _normalize_expiration(candidate_or_blocked_row.get("expiration"))
    strike = _to_optional_float(candidate_or_blocked_row.get("strike"))
    if not symbol:
        raise ValueError("option pool row requires symbol")
    if expiration is None:
        raise ValueError("option pool row requires expiration")
    if strike is None:
        raise ValueError("option pool row requires strike")

    right = str(candidate_or_blocked_row.get("right") or "P").strip().upper() or "P"
    now_iso = _normalize_datetime(now)
    quality_grade = _normalize_quality_grade(candidate_or_blocked_row.get("quality_grade"))
    quality_flags = _normalize_string_list(candidate_or_blocked_row.get("quality_flags"))
    blockers = _collect_blockers(candidate_or_blocked_row)
    explicit_blocked = _normalize_pool_status(candidate_or_blocked_row.get("status")) == "BLOCKED"
    quality_flags = _dedupe([*quality_flags, *blockers])
    status = "BLOCKED" if quality_grade == "C" or blockers or explicit_blocked else "NEW"

    return {
        "symbol": symbol,
        "expiration": expiration,
        "strike": strike,
        "right": right,
        "bid": _to_optional_float(candidate_or_blocked_row.get("bid")),
        "ask": _to_optional_float(candidate_or_blocked_row.get("ask")),
        "mid": _to_optional_float(candidate_or_blocked_row.get("mid")),
        "spot": _to_optional_float(candidate_or_blocked_row.get("spot")),
        "iv": _to_optional_float(candidate_or_blocked_row.get("iv")),
        "iv_rank": _to_optional_float(candidate_or_blocked_row.get("iv_rank")),
        "delta": _to_optional_float(candidate_or_blocked_row.get("delta")),
        "theta": _to_optional_float(candidate_or_blocked_row.get("theta")),
        "vega": _to_optional_float(candidate_or_blocked_row.get("vega")),
        "gamma": _to_optional_float(candidate_or_blocked_row.get("gamma")),
        "dte": _to_optional_int(candidate_or_blocked_row.get("dte")),
        "annualized_roi": _to_optional_float(candidate_or_blocked_row.get("annualized_roi")),
        "pop": _to_optional_float(candidate_or_blocked_row.get("pop")),
        "spread_pct": _to_optional_float(candidate_or_blocked_row.get("spread_pct")),
        "breakeven": _to_optional_float(candidate_or_blocked_row.get("breakeven")),
        "margin_buffer": _to_optional_float(candidate_or_blocked_row.get("margin_buffer")),
        "score": _to_optional_float(candidate_or_blocked_row.get("score")),
        "open_interest": _to_optional_int(candidate_or_blocked_row.get("open_interest")),
        "quality_grade": quality_grade,
        "quality_score": _to_optional_int(candidate_or_blocked_row.get("quality_score")),
        "quality_flags": quality_flags,
        "quote_age_seconds": _to_optional_int(candidate_or_blocked_row.get("quote_age_seconds")),
        "greeks_source": _optional_str(candidate_or_blocked_row.get("greeks_source")),
        "iv_rank_source": _optional_str(candidate_or_blocked_row.get("iv_rank_source")),
        "first_seen_at": now_iso,
        "last_seen_at": now_iso,
        "last_scan_run_id": int(scan_run_id) if scan_run_id is not None else None,
        "latest_candidate_id": _first_present_int(
            candidate_or_blocked_row,
            ("latest_candidate_id", "candidate_id", "id"),
        ),
        "missed_scan_count": 0,
        "status": status,
    }


def next_option_pool_status(row: Dict[str, Any], seen_this_scan: bool, today: date) -> str:
    """Return the next lifecycle status for an option_pool row."""
    expiration = _parse_date(row.get("expiration"))
    if expiration is not None and expiration < _parse_today(today):
        return "EXPIRED"

    if _is_blocked_row(row):
        return "BLOCKED"

    current = _normalize_pool_status(row.get("status"))
    if seen_this_scan:
        return "ACTIVE" if current else "NEW"

    missed = _to_optional_int(row.get("missed_scan_count")) or 0
    if missed >= STALE_MISSED_SCAN_COUNT:
        return "STALE"
    if current in OPTION_POOL_STATUSES:
        return current
    return "NEW"


def evaluate_option_watch(
    pool_row: Dict[str, Any],
    watch_row: Dict[str, Any],
    today: date,
) -> Dict[str, Any]:
    """Evaluate one option_watchlist row and return a JSON-safe status signal."""
    current = _normalize_watch_status(watch_row.get("status")) or "WATCHING"
    pool_status = _normalize_pool_status(pool_row.get("status")) or "NEW"

    if current in TERMINAL_WATCH_STATUSES:
        return _signal(current, "terminal_status", pool_status, current)

    expiration = _parse_date(pool_row.get("expiration"))
    if pool_status == "EXPIRED" or (expiration is not None and expiration < _parse_today(today)):
        return _signal("EXPIRED", "contract_expired", pool_status, current)

    if current == "READY":
        return _signal("READY", "already_ready", pool_status, current)

    if pool_status == "BLOCKED":
        return _signal("WATCHING", "pool_blocked", pool_status, current)
    if pool_status == "STALE":
        return _signal("WATCHING", "pool_stale", pool_status, current)
    if pool_status not in ACTIONABLE_POOL_STATUSES:
        return _signal("WATCHING", "pool_not_actionable", pool_status, current)

    entry_signal_status = str(pool_row.get("entry_signal_status") or "").upper()
    if entry_signal_status == "EXPIRED":
        return _signal("EXPIRED", "entry_signal_expired", pool_status, current)
    if entry_signal_status == "REJECT":
        return _signal("WATCHING", "entry_signal_reject", pool_status, current)
    if entry_signal_status in {"WAIT", "UNKNOWN"}:
        return _signal("WATCHING", "entry_signal_wait", pool_status, current)

    goal_result = _evaluate_goals(pool_row, watch_row)
    if not goal_result["goals"]:
        return _signal("READY", "no_targets_actionable", pool_status, current)
    if not goal_result["unmet"]:
        return _signal(
            "READY",
            "targets_met",
            pool_status,
            current,
            met_targets=goal_result["met"],
        )
    return _signal(
        "WATCHING",
        "targets_not_met",
        pool_status,
        current,
        met_targets=goal_result["met"],
        unmet_targets=goal_result["unmet"],
    )


def _signal(status: str, reason: str, pool_status: str, previous_status: str, **extra: Any) -> Dict[str, Any]:
    signal = {
        "status": status,
        "reason": reason,
        "pool_status": pool_status,
        "previous_status": previous_status,
    }
    signal.update(extra)
    return signal


def _evaluate_goals(pool_row: Dict[str, Any], watch_row: Dict[str, Any]) -> Dict[str, List[str]]:
    goals: List[str] = []
    met: List[str] = []
    unmet: List[str] = []

    checks = (
        ("target_premium", "mid", "premium"),
        ("target_score", "score", "score"),
        ("target_margin_buffer", "margin_buffer", "margin_buffer"),
    )
    for watch_key, pool_key, label in checks:
        target = _to_optional_float(watch_row.get(watch_key))
        if target is None:
            continue
        goals.append(label)
        actual = _to_optional_float(pool_row.get(pool_key))
        if actual is not None and actual >= target:
            met.append(label)
        else:
            unmet.append(label)
    return {"goals": goals, "met": met, "unmet": unmet}


def _is_blocked_row(row: Dict[str, Any]) -> bool:
    grade = _normalize_quality_grade(row.get("quality_grade"))
    return grade == "C" or bool(_collect_blockers(row))


def _collect_blockers(row: Dict[str, Any]) -> List[str]:
    blockers: List[str] = []
    for key in ("blocker_reasons", "blockers", "reasons"):
        blockers.extend(_normalize_string_list(row.get(key)))
    for flag in _normalize_string_list(row.get("quality_flags")):
        if flag in BLOCKER_REASONS:
            blockers.append(flag)
    return _dedupe(blockers)


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_expiration(value: Any) -> Optional[str]:
    parsed = _parse_date(value)
    return parsed.isoformat() if parsed is not None else None


def _parse_today(value: Any) -> date:
    parsed = _parse_date(value)
    if parsed is None:
        raise ValueError("today must be a date")
    return parsed


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def _normalize_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time()).isoformat()
    text = str(value or "").strip()
    if not text:
        raise ValueError("now must be a datetime")
    return text


def _normalize_quality_grade(value: Any) -> str:
    grade = str(value or "unknown").strip()
    upper = grade.upper()
    if upper in {"A", "B", "C"}:
        return upper
    return "unknown"


def _normalize_pool_status(value: Any) -> Optional[str]:
    status = str(value or "").strip().upper()
    return status if status in OPTION_POOL_STATUSES else None


def _normalize_watch_status(value: Any) -> Optional[str]:
    status = str(value or "").strip().upper()
    return status if status in OPTION_WATCH_STATUSES else None


def _normalize_string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = [text]
            return _normalize_string_list(parsed)
        return [text]
    if isinstance(value, Iterable):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


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


def _first_present_int(row: Dict[str, Any], keys: Iterable[str]) -> Optional[int]:
    for key in keys:
        value = _to_optional_int(row.get(key))
        if value is not None:
            return value
    return None


def _optional_str(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
