"""Normalize position close_reason codes for review slicing."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# Canonical buckets for review slices (not exhaustive of every legacy string).
_CLOSE_REASON_BUCKETS: Dict[str, Tuple[str, str, int]] = {
    "take_profit": ("take_profit", "止盈", 0),
    "take_profit_fast": ("take_profit_fast", "加速止盈", 1),
    "time_exit": ("time_exit", "时间退出", 2),
    "defend": ("defend", "防守", 3),
    "manual": ("manual", "手动", 4),
    "expired": ("expired", "到期", 5),
    "assigned": ("assigned", "被行权", 6),
    "unknown": ("unknown", "未知", 7),
}

_TAKE_PROFIT_PREFIXES = ("take_profit",)
_TIME_PREFIXES = ("time_",)
_DEFEND_CODES = frozenset({
    "delta_breach",
    "danger_3pct",
    "loss_breach",
    "margin_buffer_negative",
    "spot_below_strike",
})


def canonical_close_reason_code(raw: Optional[str]) -> str:
    """Map stored close_reason to a stable bucket key."""
    if not raw:
        return "unknown"
    code = str(raw).strip().lower()
    if not code:
        return "unknown"
    if code.startswith("take_profit"):
        if code == "take_profit_fast":
            return "take_profit_fast"
        return "take_profit"
    if code.startswith("time_") or code in ("time_warning", "time_danger"):
        return "time_exit"
    if code in _DEFEND_CODES or "danger" in code or "defend" in code:
        return "defend"
    if code in ("manual", "roll_extend"):
        return "manual"
    if code in ("expired_otm", "expired", "expiry_hold"):
        return "expired"
    if code == "assigned":
        return "assigned"
    return "unknown"


def close_reason_bucket(raw: Optional[str]) -> Tuple[str, str, int]:
    key = canonical_close_reason_code(raw)
    return _CLOSE_REASON_BUCKETS.get(key, _CLOSE_REASON_BUCKETS["unknown"])


def canonical_close_reason_label(raw: Optional[str]) -> str:
    return close_reason_bucket(raw)[1]


def pool_source_from_snapshot(snapshot: Dict[str, Any]) -> str:
    """How the position entered the workflow: watchlist, scan pool, or manual."""
    if snapshot.get("option_watchlist_id"):
        return "watch"
    if snapshot.get("option_pool_id"):
        return "main"
    return "manual"
