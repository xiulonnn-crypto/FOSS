"""Persist radar_snapshots rows (worker radar tick + final snapshot at close/settlement)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.db.repo import Repo


def append_radar_snapshot_from_mark(
    repo: Repo,
    position_id: int,
    taken_at_iso: str,
    mark: Dict[str, Any],
    *,
    signals: Optional[List[str]] = None,
) -> Optional[int]:
    """
    Insert one radar_snapshots row from a mark dict (same shape as mark_short_put_position).
    Returns the inserted snapshot id, or None if mark has quote_error or missing
    required fields — caller should not treat this as fatal.
    """
    if mark.get("quote_error"):
        return None
    try:
        spot = float(mark["spot"])
        mid = float(mark["option_mid"])
        pnl_pct = float(mark["pnl_pct"])
        mb = float(mark["margin_buffer"])
    except (KeyError, TypeError, ValueError):
        return None
    return repo.insert_radar_snapshot(
        {
            "position_id": position_id,
            "taken_at": taken_at_iso,
            "spot": spot,
            "current_mid": mid,
            "pnl_pct": round(pnl_pct, 4),
            "delta": mark.get("delta"),
            "margin_buffer": round(mb, 4),
            "signals": json.dumps(signals if signals is not None else []),
        }
    )
