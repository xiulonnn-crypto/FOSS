"""Backfill missing diagnostic fields in `positions.open_snapshot`.

The review condition-slices module (#review page) and the order diagnosis drawer
both bucket nine dimensions of an order from its ``open_snapshot``.  For orders
without a linked scan candidate (manual entries, legacy data, or early-prototype
positions) several fields are missing and fall into the "未知" bucket:

* ``margin_buffer``  – missing because the BS entry-recalc only wrote it on the
  per-day synthetic radar bars, not back into ``open_snapshot``.
* ``iv_rank``        – missing because there is no scan candidate to inherit it
  from; we recompute an RV-proxy from ``settings.rv_by_symbol`` (same source as
  the live screener).
* ``quality_grade``  – missing because no quality assessment ran at entry; we
  reconstruct a conservative inferred grade from existing snapshot fields.
* ``entry_signal_status`` – missing because the entry-signal card never ran for
  this position; we rebuild it deterministically from the snapshot's metrics.

The goal is for ``build_position_dimension_summary`` to stop returning the
``UNKNOWN``/``unknown`` bucket for any of those dimensions after backfill.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.core.close_reason_norm import pool_source_from_snapshot
from app.core.data_quality import infer_quality_from_candidate_snapshot
from app.core.entry_signal import ENTRY_SIGNAL_STATUSES, build_entry_signal
from app.core.strategy import compute_iv_rank
from app.db.repo import Repo

_LOG = logging.getLogger(__name__)

_GOOD_QUALITY_GRADES = {"A", "B", "C"}
_GOOD_ENTRY_SIGNAL_STATUSES = ENTRY_SIGNAL_STATUSES - {"UNKNOWN"}


def backfill_diagnostic_fields(
    repo: Repo,
    position: Dict[str, Any],
    snapshot: Dict[str, Any],
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a new snapshot dict with the four diagnostic-unknown fields filled.

    Idempotent: skips a field when the snapshot already carries a good value.
    Never mutates the input snapshot dict.
    """
    settings = settings or {}
    out = dict(snapshot or {})

    _backfill_margin_buffer(out, position)
    _backfill_pool_source(out)
    _backfill_iv_rank(out, position, settings)
    _backfill_quality_grade(out, position, settings)
    _backfill_entry_signal(out, position, settings)

    return out


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _backfill_margin_buffer(snapshot: Dict[str, Any], position: Dict[str, Any]) -> None:
    if _to_float(snapshot.get("margin_buffer")) is not None:
        return
    spot = _to_float(snapshot.get("spot"))
    strike = _to_float(snapshot.get("strike")) or _to_float(position.get("strike"))
    if spot is None or strike is None or spot <= 0:
        return
    snapshot["margin_buffer"] = round((spot - strike) / spot, 6)


def _backfill_pool_source(snapshot: Dict[str, Any]) -> None:
    """Ensure `pool_source` is always one of {main, watch, manual}."""
    current = str(snapshot.get("pool_source") or "").strip().lower()
    if current in ("main", "watch", "manual"):
        return
    snapshot["pool_source"] = pool_source_from_snapshot(snapshot)


def _backfill_iv_rank(
    snapshot: Dict[str, Any],
    position: Dict[str, Any],
    settings: Dict[str, Any],
) -> None:
    if _to_float(snapshot.get("iv_rank")) is not None:
        return
    iv = _to_float(snapshot.get("iv"))
    if iv is None:
        return
    symbol = str(position.get("symbol") or "").strip().upper()
    if not symbol:
        return
    rv_history = (settings.get("rv_by_symbol") or {}).get(symbol)
    if not isinstance(rv_history, list) or len(rv_history) < 5:
        return
    rank = compute_iv_rank(iv, rv_history)
    if rank is None:
        return
    snapshot["iv_rank"] = rank
    snapshot.setdefault("iv_rank_source", "rv_proxy")


def _backfill_quality_grade(
    snapshot: Dict[str, Any],
    position: Dict[str, Any],
    settings: Dict[str, Any],
) -> None:
    current = str(snapshot.get("quality_grade") or "").strip().upper()
    if current in _GOOD_QUALITY_GRADES:
        return

    row = _candidate_like_row(snapshot, position)
    inferred = infer_quality_from_candidate_snapshot(row, settings)

    if inferred is not None:
        snapshot["quality_grade"] = inferred.grade
        snapshot["quality_score"] = inferred.score
        snapshot["quality_flags"] = _merge_flags(snapshot.get("quality_flags"), inferred.flags)
        snapshot.setdefault("greeks_source", inferred.greeks_source)
        snapshot.setdefault("iv_rank_source", inferred.iv_rank_source)
        if inferred.quote_age_seconds is not None:
            snapshot.setdefault("quote_age_seconds", inferred.quote_age_seconds)
        return

    snapshot["quality_grade"] = "B"
    snapshot["quality_score"] = int(_to_float(snapshot.get("quality_score")) or 70)
    snapshot["quality_flags"] = _merge_flags(
        snapshot.get("quality_flags"),
        ["snapshot_inferred", "manual_entry"],
    )


def _backfill_entry_signal(
    snapshot: Dict[str, Any],
    position: Dict[str, Any],
    settings: Dict[str, Any],
) -> None:
    current = str(snapshot.get("entry_signal_status") or "").strip().upper()
    if current in _GOOD_ENTRY_SIGNAL_STATUSES:
        return

    row = _candidate_like_row(snapshot, position)
    try:
        signal = build_entry_signal(pool_row=row, candidate_row=None, settings=settings)
    except Exception as exc:
        _LOG.warning(
            "review_backfill: build_entry_signal failed for position_id=%s: %s",
            position.get("id"),
            exc,
        )
        return

    status = str(signal.get("status") or "").upper()
    if status not in _GOOD_ENTRY_SIGNAL_STATUSES:
        status = "WAIT"
        signal = dict(signal)
        signal["status"] = status
        signal.setdefault("summary", "回填诊断：缺少完整入场快照，按等待处理。")

    snapshot["entry_signal_status"] = status
    snapshot["entry_signal_score"] = int(_to_float(signal.get("decision_score")) or 0)
    snapshot["entry_signal_summary"] = signal.get("summary")
    snapshot["entry_signal"] = signal


def _candidate_like_row(snapshot: Dict[str, Any], position: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten snapshot+position into a dict shaped like a candidate / pool row."""
    row: Dict[str, Any] = dict(snapshot or {})
    row.setdefault("symbol", position.get("symbol"))
    row.setdefault("expiration", position.get("expiration"))
    row.setdefault("strike", position.get("strike"))
    row.setdefault("right", "P")
    open_premium = _to_float(position.get("open_premium"))
    if row.get("mid") is None and open_premium is not None and open_premium > 0:
        row["mid"] = open_premium
    if row.get("bid") is None and open_premium is not None and open_premium > 0:
        row["bid"] = open_premium
    if row.get("ask") is None and open_premium is not None and open_premium > 0:
        row["ask"] = open_premium
    if row.get("open_interest") is None:
        row["open_interest"] = 50
    if row.get("spread_pct") is None:
        row["spread_pct"] = 0.0
    return row


def _merge_flags(existing: Any, new: Any) -> list:
    out: list = []
    seen: set = set()
    for source in (existing or [], new or []):
        if isinstance(source, str):
            source = [source]
        if not isinstance(source, (list, tuple)):
            continue
        for value in source:
            text = str(value)
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
    return out
