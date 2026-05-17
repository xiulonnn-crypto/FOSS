"""Build `positions.open_snapshot` JSON from candidate row + historical OHLC (yfinance)."""

from __future__ import annotations

import logging
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.technicals import compute_bb_lower_distance_pct, compute_rsi
from app.core.time_et import APP_TZ
from app.db.repo import Repo

_LOG = logging.getLogger(__name__)

_CANDIDATE_FIELDS = (
    "iv_rank",
    "iv",
    "delta",
    "theta",
    "vega",
    "spot",
    "dte",
    "annualized_roi",
    "score",
    "margin_buffer",
)

_QUALITY_FIELDS = (
    "quality_grade",
    "quality_score",
    "quality_flags",
    "quote_age_seconds",
    "greeks_source",
    "iv_rank_source",
)

_POOL_REFERENCE_FIELDS = (
    "option_pool_id",
    "option_watchlist_id",
)

_ENTRY_SIGNAL_FIELDS = (
    "entry_signal_id",
    "entry_signal_status",
    "entry_signal_score",
    "entry_signal_summary",
)


def _merge_candidate_fields_from_request(snapshot: Dict[str, Any], req: Dict[str, Any]) -> None:
    """Fill Greeks / entry metrics from POST /positions body when scan row had no DB id (e.g. specific search)."""
    for field in _CANDIDATE_FIELDS:
        if snapshot.get(field) is not None:
            continue
        val = req.get(field)
        if val is None or val == "":
            continue
        try:
            if field == "dte":
                snapshot[field] = int(float(val))
            else:
                snapshot[field] = float(val)
        except (TypeError, ValueError):
            continue
    for field in _QUALITY_FIELDS:
        if snapshot.get(field) is not None:
            continue
        val = req.get(field)
        if val is None or val == "":
            continue
        if field in ("quality_score", "quote_age_seconds"):
            try:
                snapshot[field] = int(float(val))
            except (TypeError, ValueError):
                continue
        elif field == "quality_flags":
            snapshot[field] = val if isinstance(val, list) else [str(val)]
        else:
            snapshot[field] = str(val)
    for field in _POOL_REFERENCE_FIELDS:
        if snapshot.get(field) is not None:
            continue
        val = req.get(field)
        if val is None or val == "":
            continue
        try:
            snapshot[field] = int(float(val))
        except (TypeError, ValueError):
            continue
    for field in _ENTRY_SIGNAL_FIELDS:
        if snapshot.get(field) is not None:
            continue
        val = req.get(field)
        if val is None or val == "":
            continue
        if field in {"entry_signal_id", "entry_signal_score"}:
            try:
                snapshot[field] = int(float(val))
            except (TypeError, ValueError):
                continue
        else:
            snapshot[field] = str(val)
    if snapshot.get("entry_signal") is None and req.get("entry_signal") is not None:
        val = req.get("entry_signal")
        if isinstance(val, dict):
            snapshot["entry_signal"] = val
        elif isinstance(val, str):
            try:
                parsed = json.loads(val)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                snapshot["entry_signal"] = parsed


def position_open_datetime(pos: Dict[str, Any]) -> Optional[datetime]:
    """Entry instant in normalized UTC (same rules as ``parse_instant_utc``)."""
    from app.core.time_et import parse_instant_utc

    return parse_instant_utc(pos.get("open_at"))


def closes_through_entry(symbol: str, entry_dt: datetime) -> Optional[List[float]]:
    """
    Daily adjusted closes from oldest through last session on or before entry (US Eastern calendar).
    """
    try:
        import yfinance as yf

        entry_date = entry_dt.astimezone(APP_TZ).date()
        start = entry_date - timedelta(days=400)
        end_excl = entry_date + timedelta(days=1)
        ticker = yf.Ticker(symbol)
        hist = ticker.history(
            start=start.isoformat(),
            end=end_excl.isoformat(),
            interval="1d",
            auto_adjust=True,
        )
        if hist.empty:
            return None
        closes: List[float] = []
        for ts, row in hist.iterrows():
            bar_date = ts.date() if hasattr(ts, "date") else ts
            if bar_date <= entry_date:
                closes.append(float(row["Close"]))
        if len(closes) < 7:
            return None
        return closes
    except Exception as exc:
        _LOG.info("open_snapshot: history fetch skipped for %s: %s", symbol, exc)
        return None


def build_open_snapshot_dict(
    repo: Repo,
    pos: Dict[str, Any],
    request_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Merge scan candidate fields (if linked) with RSI/bollinger metrics at entry.
    `request_data` is optional body from POST /positions (open_candidate_id plus
    iv/delta/theta/… when the UI row has no DB candidate id, e.g. specific search).
    """
    snapshot: Dict[str, Any] = {}
    req = request_data or {}

    cand_id = req.get("open_candidate_id")
    if cand_id is None:
        cand_id = pos.get("open_candidate_id")
    if cand_id:
        try:
            cand = repo.get_candidate_by_id(int(cand_id))
            if cand:
                for field in (*_CANDIDATE_FIELDS, *_QUALITY_FIELDS):
                    if cand.get(field) is not None:
                        snapshot[field] = cand[field]
        except Exception:
            pass

    _merge_candidate_fields_from_request(snapshot, req)

    symbol = (pos.get("symbol") or "").upper().strip()
    entry_dt = position_open_datetime(pos)
    if snapshot.get("option_watchlist_id"):
        snapshot["pool_source"] = "watch"
    elif snapshot.get("option_pool_id"):
        snapshot["pool_source"] = "main"
    else:
        snapshot["pool_source"] = "manual"

    if symbol and entry_dt:
        closes = closes_through_entry(symbol, entry_dt)
        if closes:
            rsi_6 = compute_rsi(closes, 6)
            rsi_12 = compute_rsi(closes, 12)
            rsi_24 = compute_rsi(closes, 24)
            bb_dist = compute_bb_lower_distance_pct(closes, window=20)
            if rsi_6 is not None:
                snapshot["rsi_6"] = rsi_6
            if rsi_12 is not None:
                snapshot["rsi_12"] = rsi_12
            if rsi_24 is not None:
                snapshot["rsi_24"] = rsi_24
            if bb_dist is not None:
                snapshot["bb_distance_pct"] = bb_dist

    return snapshot
