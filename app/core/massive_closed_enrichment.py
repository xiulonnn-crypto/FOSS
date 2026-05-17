"""Best-effort Massive EOD option aggregates merged into ``positions.open_snapshot``."""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.option_ticker_osi import format_osi_option_ticker
from app.core.pnl_excursion import (
    relative_mae_mfe_from_pnls_chronologic,
    short_put_premium_pnl_pct,
)
from app.core.time_et import APP_TZ
from app.data.massive_client import MassiveClient, aggs_bar_date_et_ms
from app.db.repo import Repo

_LOG = logging.getLogger(__name__)

_CLOSED_STATES = frozenset({"CLOSED_EARLY", "EXPIRED_OTM", "ASSIGNED"})
_HISTORY_DAYS_MAX = 730


def _massive_enrich_enabled(repo: Repo) -> bool:
    """
    Prefer explicit env overrides so MASSIVE_API_KEY in .env can work without
    opening SQLite settings.

    ``MASSIVE_ENRICH_CLOSED=0|false|off`` — force off.
    ``MASSIVE_ENRICH_CLOSED=1|true|on`` — force on (still needs ``MASSIVE_API_KEY``).
    Otherwise fall back to ``integrations.massive_enrich_closed`` in settings.
    """
    env_raw = (os.environ.get("MASSIVE_ENRICH_CLOSED") or "").strip().lower()
    if env_raw in ("0", "false", "off", "no"):
        return False
    if env_raw in ("1", "true", "on", "yes"):
        return True

    s = repo.get_settings() or {}
    block = s.get("integrations") or {}
    v = block.get("massive_enrich_closed")
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return False


def _open_calendar_date(pos: Dict[str, Any]) -> Optional[date]:
    raw = pos.get("open_at")
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(APP_TZ).date()


def _close_calendar_date(pos: Dict[str, Any]) -> Optional[date]:
    raw = pos.get("close_at")
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(APP_TZ).date()


def _clip_range(open_d: date, close_d: date) -> Optional[tuple[date, date]]:
    """Clip to Massive free-tier history window (2y) ending today."""
    today = datetime.now(timezone.utc).astimezone(APP_TZ).date()
    end_d = min(close_d, today)
    earliest = today - timedelta(days=_HISTORY_DAYS_MAX)
    start_d = max(open_d, earliest)
    if start_d > end_d:
        return None
    return start_d, end_d


def enrich_closed_position_open_snapshot_massive(repo: Repo, position_id: int) -> None:
    """
    If ``MASSIVE_API_KEY`` and ``integrations.massive_enrich_closed`` are set, fetch
    daily option OHLC and store EOD-based MAE/MFE under ``open_snapshot.massive``.
    Never raises; logs at INFO on skip/failure.
    """
    if not _massive_enrich_enabled(repo):
        return
    api_key = (os.environ.get("MASSIVE_API_KEY") or "").strip()
    if not api_key:
        _LOG.info("massive enrich skipped: MASSIVE_API_KEY unset position_id=%s", position_id)
        return

    pos = repo.get_position(position_id)
    if not pos:
        return
    st = str(pos.get("state") or "")
    if st not in _CLOSED_STATES:
        return

    symbol = str(pos.get("symbol") or "").upper().strip()
    exp_raw = str(pos.get("expiration") or "").strip()[:10]
    try:
        exp_d = date.fromisoformat(exp_raw)
    except ValueError:
        _LOG.info("massive enrich skipped: bad expiration position_id=%s", position_id)
        return

    open_d = _open_calendar_date(pos)
    close_d = _close_calendar_date(pos)
    if open_d is None or close_d is None:
        _LOG.info("massive enrich skipped: missing dates position_id=%s", position_id)
        return

    rng = _clip_range(open_d, close_d)
    if rng is None:
        _LOG.info("massive enrich skipped: range outside history window position_id=%s", position_id)
        return
    start_d, end_d = rng

    strike = float(pos.get("strike") or 0)
    open_premium = float(pos.get("open_premium") or 0)
    if strike <= 0 or open_premium <= 0:
        return

    try:
        opt_ticker = format_osi_option_ticker(symbol, exp_d, "P", strike)
    except ValueError as exc:
        _LOG.info("massive enrich skipped: %s position_id=%s", exc, position_id)
        return

    client = MassiveClient(api_key)
    rows = client.fetch_daily_aggs(opt_ticker, start_d, end_d)
    if not rows:
        _LOG.info("massive enrich: no aggregates position_id=%s ticker=%s", position_id, opt_ticker)
        return

    # One synthetic PnL% per ET calendar session (later row overwrites duplicate day if any).
    pnl_by_day: Dict[date, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        t_ms = row.get("t")
        c = row.get("c")
        if t_ms is None or c is None:
            continue
        try:
            bar_d = aggs_bar_date_et_ms(int(t_ms))
            oc = float(c)
        except (TypeError, ValueError):
            continue
        if bar_d < open_d or bar_d > close_d:
            continue
        pnl_by_day[bar_d] = short_put_premium_pnl_pct(open_premium, oc)

    if not pnl_by_day:
        _LOG.info("massive enrich: no bars in hold window position_id=%s", position_id)
        return

    pnl_sorted_chrono = [pnl_by_day[d] for d in sorted(pnl_by_day.keys())]
    mae_obs, mfe_obs = relative_mae_mfe_from_pnls_chronologic(pnl_sorted_chrono)

    taken = datetime.now(timezone.utc).isoformat()

    block: Dict[str, Any] = {
        "source": "massive",
        "model": "eod_option_daily_close",
        "fetched_at": taken,
        "option_ticker": opt_ticker,
        "bar_count": len(pnl_sorted_chrono),
        "mae_pnl_pct": None if mae_obs is None else round(float(mae_obs), 6),
        "mfe_pnl_pct": None if mfe_obs is None else round(float(mfe_obs), 6),
        "basis": "first_bar_excursion_pct",
        # 核对用：open_at / close_at 转 APP_TZ 日历日后的闭区间 [open_date_et, close_date_et]；
        # 仅用 bar_d（由 Massive 日柱时间戳转成的美东会话日）落在此区间内的收盘价；盘中平仓时最后一根 EOD 可能晚于平仓时刻。
        "hold_window": {
            "open_date_et": open_d.isoformat(),
            "close_date_et": close_d.isoformat(),
        },
        "fetch_clip": {
            "start_date_et": start_d.isoformat(),
            "end_date_et": end_d.isoformat(),
        },
    }

    prev = repo.get_open_snapshot(position_id) or {}
    merged = dict(prev) if isinstance(prev, dict) else {}
    merged["massive"] = block
    repo.save_open_snapshot(position_id, merged)
    _LOG.info(
        "massive enrich ok position_id=%s ticker=%s bars=%s",
        position_id,
        opt_ticker,
        len(pnl_sorted_chrono),
    )
