"""标的 OHLC 蜡烛序列（复盘抽屉用）。

为单笔已平仓持仓拉取标的的 **小时** K 线（yfinance ``1h``，覆盖近 ~2 年），
并标出入场 / 出场的时间点与对应标的价位置，供前端在成交订单详情抽屉里画蜡烛图。

- 默认 ``1h``；当持仓过老（超出 yfinance 小时历史窗口）时回退 ``1d``。
- 时间范围 = 持仓 ET ``[open_date .. close_date]`` 前后各加 padding，给出价格上下文。
- ``entry.spot`` 取 ``open_snapshot.spot``（回退候选 ``spot``）。
- ``exit.spot``  取 ``close_snapshot.mark.spot``。

永不抛出：取数失败或缺历史时返回 ``{"available": False, ...}``。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.time_et import APP_TZ, parse_instant_utc
from app.db.repo import Repo

_LOG = logging.getLogger(__name__)

# yfinance 1h 历史大约只回溯 ~730 天；留点裕量用 700 天判定。
_HOURLY_MAX_AGE_DAYS = 700
_CLOSED_STATES = frozenset({"CLOSED_EARLY", "EXPIRED_OTM", "ASSIGNED"})


def _cal_date_et(raw: Any) -> Optional[date]:
    dt = parse_instant_utc(raw)
    if dt is None:
        return None
    return dt.astimezone(APP_TZ).date()


def _bar_ts_utc_iso(ts: Any) -> Optional[str]:
    """yfinance 行索引 → 带时区的 UTC ISO 字符串。"""
    try:
        import pandas as pd

        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            t = t.tz_localize(APP_TZ)
        return t.tz_convert("UTC").to_pydatetime().isoformat()
    except Exception:
        return None


def _fetch_ohlc(
    symbol: str,
    start_d: date,
    end_d: date,
    interval: str,
) -> List[Dict[str, Any]]:
    """拉取 ``[start_d, end_d]``（含尾）区间内的 OHLC 柱。失败返回空列表。"""
    try:
        import yfinance as yf
    except ImportError:
        return []

    end_fetch = end_d + timedelta(days=1)  # yfinance end 为开区间
    try:
        hist = yf.Ticker(symbol).history(
            start=start_d.isoformat(),
            end=end_fetch.isoformat(),
            interval=interval,
            auto_adjust=True,
        )
    except Exception as exc:
        _LOG.info("underlying_candles: yf %s %s fetch failed: %s", symbol, interval, exc)
        return []

    if hist is None or hist.empty:
        return []

    out: List[Dict[str, Any]] = []
    for ts, row in hist.iterrows():
        iso = _bar_ts_utc_iso(ts)
        if iso is None:
            continue
        try:
            o = float(row["Open"])
            h = float(row["High"])
            low = float(row["Low"])
            c = float(row["Close"])
        except Exception:
            continue
        if o <= 0 or h <= 0 or low <= 0 or c <= 0:
            continue
        out.append({"t": iso, "o": o, "h": h, "l": low, "c": c})
    out.sort(key=lambda b: b["t"])
    return out


def _entry_spot(repo: Repo, position_id: int, pos: Dict[str, Any]) -> Optional[float]:
    snap = None
    try:
        snap = repo.get_open_snapshot(position_id)
    except AttributeError:
        snap = None
    if isinstance(snap, dict) and snap.get("spot") is not None:
        try:
            v = float(snap["spot"])
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    cand_id = pos.get("open_candidate_id")
    if cand_id is not None:
        try:
            cand = repo.get_candidate_by_id(cand_id)
        except AttributeError:
            cand = None
        if isinstance(cand, dict) and cand.get("spot") is not None:
            try:
                v = float(cand["spot"])
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
    return None


def _exit_spot(pos: Dict[str, Any]) -> Optional[float]:
    cs = pos.get("close_snapshot")
    if isinstance(cs, dict):
        mark = cs.get("mark")
        if isinstance(mark, dict) and mark.get("spot") is not None:
            try:
                v = float(mark["spot"])
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
    return None


def build_underlying_candles(repo: Repo, position_id: int) -> Dict[str, Any]:
    """返回标的蜡烛序列 + 入场/出场标记。永不抛出。"""
    pos = repo.get_position(position_id)
    if not pos:
        return {"available": False, "reason": "position_not_found"}
    if str(pos.get("state") or "") not in _CLOSED_STATES:
        return {"available": False, "reason": "position_not_closed"}

    symbol = str(pos.get("symbol") or "").upper().strip()
    if not symbol:
        return {"available": False, "reason": "no_symbol"}

    open_utc = parse_instant_utc(pos.get("open_at"))
    close_utc = parse_instant_utc(pos.get("close_at"))
    open_d = _cal_date_et(pos.get("open_at"))
    close_d = _cal_date_et(pos.get("close_at"))
    if open_d is None or close_d is None:
        return {"available": False, "reason": "missing_dates"}
    if close_d < open_d:
        open_d, close_d = close_d, open_d

    today = datetime.now(timezone.utc).astimezone(APP_TZ).date()
    open_age_days = (today - open_d).days

    if open_age_days <= _HOURLY_MAX_AGE_DAYS:
        interval = "1h"
        pad_before, pad_after = 1, 1
    else:
        interval = "1d"
        pad_before, pad_after = 5, 5

    start_d = open_d - timedelta(days=pad_before)
    end_d = min(close_d + timedelta(days=pad_after), today)

    candles = _fetch_ohlc(symbol, start_d, end_d, interval)
    # 小时数据偶发拿不到（节假日/新上市/限流）时回退日线兜底。
    if not candles and interval == "1h":
        interval = "1d"
        start_d = open_d - timedelta(days=5)
        end_d = min(close_d + timedelta(days=5), today)
        candles = _fetch_ohlc(symbol, start_d, end_d, interval)

    if not candles:
        return {"available": False, "reason": "no_candles", "symbol": symbol}

    entry = {
        "t": open_utc.isoformat() if open_utc else None,
        "spot": _entry_spot(repo, position_id, pos),
    }
    exit_marker = {
        "t": close_utc.isoformat() if close_utc else None,
        "spot": _exit_spot(pos),
    }

    return {
        "available": True,
        "position_id": position_id,
        "symbol": symbol,
        "interval": interval,
        "candles": candles,
        "entry": entry,
        "exit": exit_marker,
    }
