"""Stock High/Low (session or hold-window) × BS → MAE/MFE.

Hold calendar [open_date_et .. close_date_et] (America/New_York):

* **Cross-day**: Yahoo **daily** ``Low``/``High``; IV from Massive EOD back-fit per day when
  available (else snapshot IV).

* **Same ET calendar day** (hold window): Yahoo minute/hour bars clipped to ``[open_at, close_at]``.
  BS IV is **implied from ``open_premium`` at snapshot spot** when solvable; otherwise snapshot IV.
  Anchor mark then aligns with the fill so MAE/MFE are not polluted by snapshot IV vs premium mismatch.

First series point: BS at snapshot ``spot`` using the same IV as window extremes; with fill-implied
IV this reproduces ``open_premium`` (~0% anchor). Without ``spot``, anchor ``0.0``.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.greeks import black_scholes_price, implied_vol_black_scholes_put
from app.core.option_ticker_osi import format_osi_option_ticker
from app.core.pnl_excursion import (
    relative_mae_mfe_from_pnls_chronologic,
    short_put_premium_pnl_pct,
)
from app.core.time_et import APP_TZ, parse_instant_utc
from app.data.massive_client import MassiveClient, aggs_bar_date_et_ms
from app.db.repo import Repo

_LOG = logging.getLogger(__name__)
_RISK_FREE_RATE = 0.045
_HISTORY_DAYS_MAX = 730  # 2 years, mirrors Massive free-tier cap

_CLOSED_STATES = frozenset({"CLOSED_EARLY", "EXPIRED_OTM", "ASSIGNED"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cal_date_et(raw: Any) -> Optional[date]:
    """Parse an ISO-8601 instant string → America/New_York calendar date."""
    if not raw:
        return None
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(APP_TZ).date()


def _hist_bar_ts_utc(ts: Any) -> datetime:
    """Normalize yfinance row index → aware UTC ``datetime``."""
    import pandas as pd

    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize(APP_TZ)
    return t.astimezone(timezone.utc).to_pydatetime()


_YF_INTRADAY_FLOOR_TD = {"1m": timedelta(minutes=1), "5m": timedelta(minutes=5), "1h": timedelta(hours=1)}


def _bar_overlaps_hold_window(su_utc: datetime, eu_utc: datetime, bar_open_utc: datetime, iv: str) -> bool:
    """
    Whether an intraday bar (yfinance candle indexed at open time) overlaps
    inclusive hold bounds ``[su_utc, eu_utc]`` in UTC.

    Candles treated as ``[bar_open_utc, bar_open_utc + Δ)``.

    Uses strict ``bar_open_utc < eu_utc`` so that a candle that opens exactly at
    close_at is NOT included — the position was already closed at that moment and
    the bar captures post-close price action.
    """
    floor = _YF_INTRADAY_FLOOR_TD.get(iv)
    if floor is None:
        floor = timedelta(minutes=5)
    bar_end = bar_open_utc + floor
    return bar_open_utc < eu_utc and bar_end > su_utc


def _yf_stock_min_max_between(
    symbol: str,
    start_utc: datetime,
    end_utc: datetime,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Min ``Low`` / max ``High`` among intraday yfinance bars that **overlap**
    ``[start_utc, end_utc)`` (UTC, strict upper bound).  Bar index is candle open.

    Also returns ``entry_approx``: the Open price of the bar that **contains**
    ``start_utc`` (i.e. the candle whose open ≤ start_utc < open + interval).  This is
    a better proxy for the actual stock price at fill time than the potentially stale
    snapshot ``spot`` field, especially for same-day positions opened hours after the
    radar snapshot was taken.

    Returns ``(None, None, None)`` outside intraday history limits or on failure.
    """
    try:
        import yfinance as yf
    except ImportError:
        return None, None

    su = (
        start_utc.astimezone(timezone.utc)
        if start_utc.tzinfo
        else start_utc.replace(tzinfo=timezone.utc)
    )
    eu = (
        end_utc.astimezone(timezone.utc)
        if end_utc.tzinfo
        else end_utc.replace(tzinfo=timezone.utc)
    )
    if eu < su:
        return None, None, None

    now = datetime.now(timezone.utc)
    age_days = (now - eu).total_seconds() / 86400.0
    # Prefer 1m while Yahoo still serves it (~8d); 7.tight cutoff falsely chose 5m
    # and hid sub-candle fills that only overlap (bar_open < open_at).
    if age_days <= 10:
        interval = "1m"
    elif age_days <= 59:
        interval = "5m"
    elif age_days <= 730:
        interval = "1h"
    else:
        return None, None, None

    pad_start = su.date() - timedelta(days=1)
    pad_end = eu.date() + timedelta(days=2)
    try:
        hist = yf.Ticker(symbol).history(
            start=pad_start.isoformat(),
            end=pad_end.isoformat(),
            interval=interval,
            auto_adjust=True,
        )
    except Exception as exc:
        _LOG.info("intraday_bs: yf hold-window %s %s failed: %s", symbol, interval, exc)
        return None, None

    if hist is None or hist.empty:
        return None, None, None

    bar_floor = _YF_INTRADAY_FLOOR_TD.get(interval, timedelta(minutes=5))
    lo_v: Optional[float] = None
    hi_v: Optional[float] = None
    entry_approx: Optional[float] = None
    for ts, row in hist.iterrows():
        try:
            ts_utc = _hist_bar_ts_utc(ts)
        except Exception:
            continue
        if not _bar_overlaps_hold_window(su, eu, ts_utc, interval):
            continue
        try:
            rl = float(row["Low"])
            rh = float(row["High"])
            ro = float(row["Open"])
        except Exception:
            continue
        if rl <= 0 or rh <= 0:
            continue
        lo_v = rl if lo_v is None else min(lo_v, rl)
        hi_v = rh if hi_v is None else max(hi_v, rh)
        # Entry bar: the bar whose interval contains start_utc (ts_utc ≤ su < ts_utc+Δ).
        # Its Open is the best available proxy for the stock price at fill time.
        if entry_approx is None and ts_utc <= su < ts_utc + bar_floor and ro > 0:
            entry_approx = ro

    if lo_v is None or hi_v is None:
        return None, None, None
    return lo_v, hi_v, entry_approx


def _fetch_stock_daily_hl(
    symbol: str,
    start_d: date,
    end_d: date,
) -> List[Tuple[date, float, float]]:
    """
    Return ``[(bar_date, low, high), ...]`` for each trading day in [start_d, end_d].
    Uses yfinance 1d bars (available for 5+ years).
    Returns empty list on failure.
    """
    try:
        import yfinance as yf
    except ImportError:
        return []

    end_fetch = end_d + timedelta(days=1)
    try:
        hist = yf.Ticker(symbol).history(
            start=start_d.isoformat(),
            end=end_fetch.isoformat(),
            interval="1d",
            auto_adjust=True,
        )
    except Exception as exc:
        _LOG.info("intraday_bs: yf daily fetch failed %s: %s", symbol, exc)
        return []

    if hist is None or hist.empty:
        return []

    out: List[Tuple[date, float, float]] = []
    for ts, row in hist.iterrows():
        try:
            bar_d = ts.date() if hasattr(ts, "date") else ts
            if bar_d < start_d or bar_d > end_d:
                continue
            lo = float(row["Low"])
            hi = float(row["High"])
            if lo <= 0 or hi <= 0:
                continue
            out.append((bar_d, lo, hi))
        except Exception:
            continue
    out.sort(key=lambda x: x[0])
    return out


def _fetch_eod_iv_map(
    api_key: str,
    opt_ticker: str,
    start_d: date,
    end_d: date,
    symbol: str,
    strike: float,
    exp_d: date,
    rate: float,
) -> Dict[date, float]:
    """
    Build ``{date: implied_vol}`` by back-fitting IV from Massive option EOD close
    against yfinance stock daily close on the same day.
    Returns empty dict when Massive key is absent or returns no usable data.
    """
    if not api_key:
        return {}

    try:
        import yfinance as yf
        end_fetch = end_d + timedelta(days=1)
        stock_hist = yf.Ticker(symbol).history(
            start=start_d.isoformat(),
            end=end_fetch.isoformat(),
            interval="1d",
            auto_adjust=True,
        )
        stock_eod: Dict[date, float] = {}
        if stock_hist is not None and not stock_hist.empty:
            for ts, row in stock_hist.iterrows():
                try:
                    bar_d = ts.date() if hasattr(ts, "date") else ts
                    stock_eod[bar_d] = float(row["Close"])
                except Exception:
                    pass
    except Exception as exc:
        _LOG.info("intraday_bs: yf stock daily for IV failed %s: %s", symbol, exc)
        return {}

    client = MassiveClient(api_key)
    rows = client.fetch_daily_aggs(opt_ticker, start_d, end_d)
    if not rows:
        return {}

    iv_map: Dict[date, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        t_ms = row.get("t")
        c = row.get("c")
        if t_ms is None or c is None:
            continue
        try:
            bar_d = aggs_bar_date_et_ms(int(t_ms))
            opt_c = float(c)
        except (TypeError, ValueError):
            continue
        spot_eod = stock_eod.get(bar_d)
        if spot_eod is None or spot_eod <= 0:
            continue
        dte_eod = max((exp_d - bar_d).days, 0)
        t_eod = max(dte_eod / 365.0, 1e-9)
        iv = implied_vol_black_scholes_put(spot_eod, strike, rate, t_eod, opt_c)
        if iv is not None and 0.01 <= iv <= 5.0:
            iv_map[bar_d] = iv

    return iv_map


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def enrich_closed_position_intraday_bs(repo: Repo, position_id: int) -> None:
    """
    Compute MAE/MFE for a closed short-put (BS mark vs entry premium) and persist
    under ``open_snapshot.intraday_bs``.

* **Same ET calendar day** open/close: Yahoo intraday bars clipped to
  ``[open_at, close_at]`` for stock min/max (``hold_window_hl``); BS IV is
  **entry snapshot IV** throughout (aligned with anchor at snapshot spot).

* **Otherwise**: Yahoo **daily** Low/High; IV from Massive EOD back-fit per day when

    First series point is BS mark at snapshot ``spot`` vs ``open_premium`` when
    ``spot`` is present; otherwise ``0``.

    IV: Massive EOD back-fit per day when available; else snapshot IV constant.

    Never raises; errors are logged at INFO.
    """
    try:
        import yfinance  # noqa: F401 – fast availability check
    except ImportError:
        return

    pos = repo.get_position(position_id)
    if not pos:
        return
    if str(pos.get("state") or "") not in _CLOSED_STATES:
        return

    symbol = str(pos.get("symbol") or "").upper().strip()
    exp_raw = str(pos.get("expiration") or "").strip()[:10]
    try:
        exp_d = date.fromisoformat(exp_raw)
    except ValueError:
        return

    open_d = _cal_date_et(pos.get("open_at"))
    close_d = _cal_date_et(pos.get("close_at"))
    if open_d is None or close_d is None:
        return

    strike = float(pos.get("strike") or 0)
    open_premium = float(pos.get("open_premium") or 0)
    if strike <= 0 or open_premium <= 0:
        return

    try:
        opt_ticker = format_osi_option_ticker(symbol, exp_d, "P", strike)
    except ValueError as exc:
        _LOG.info("intraday_bs skipped: %s position_id=%s", exc, position_id)
        return

    today = datetime.now(timezone.utc).astimezone(APP_TZ).date()
    earliest = today - timedelta(days=_HISTORY_DAYS_MAX)
    start_d = max(open_d, earliest)
    end_d_clip = min(close_d, today)
    if start_d > end_d_clip:
        _LOG.info("intraday_bs skipped: range outside history window position_id=%s", position_id)
        return

    open_utc = parse_instant_utc(pos.get("open_at"))
    close_utc = parse_instant_utc(pos.get("close_at"))

    interval_tag = "1d_hl"
    hold_window_fallback = False
    hw_entry_approx: Optional[float] = None
    day_bars: List[Tuple[date, float, float]]
    if (
        open_d == close_d
        and open_utc is not None
        and close_utc is not None
        and start_d <= open_d <= end_d_clip
    ):
        hw_lo, hw_hi, hw_entry_approx = _yf_stock_min_max_between(symbol, open_utc, close_utc)
        if hw_lo is not None and hw_hi is not None:
            day_bars = [(open_d, hw_lo, hw_hi)]
            interval_tag = "hold_window_hl"
        else:
            day_bars = _fetch_stock_daily_hl(symbol, start_d, end_d_clip)
            hold_window_fallback = True
    else:
        day_bars = _fetch_stock_daily_hl(symbol, start_d, end_d_clip)

    if not day_bars:
        _LOG.info("intraday_bs: no stock H/L bars position_id=%s", position_id)
        return

    # --- IV map (Massive EOD back-fit) ---
    api_key = (os.environ.get("MASSIVE_API_KEY") or "").strip()
    iv_map = _fetch_eod_iv_map(
        api_key, opt_ticker, start_d, end_d_clip, symbol, strike, exp_d, _RISK_FREE_RATE
    )

    snap = repo.get_open_snapshot(position_id) or {}
    raw_entry_iv = snap.get("iv")
    try:
        fallback_iv: float = (
            float(raw_entry_iv) if (raw_entry_iv is not None and float(raw_entry_iv) > 0) else 0.30
        )
    except (TypeError, ValueError):
        fallback_iv = 0.30

    raw_spot = snap.get("spot")
    try:
        snap_spot = float(raw_spot) if raw_spot is not None and float(raw_spot) > 0 else 0.0
    except (TypeError, ValueError):
        snap_spot = 0.0

    # For hold-window mode prefer the yfinance bar Open at open_at as a more accurate
    # proxy for the stock price at fill time.  Snapshot.spot may be stale (captured hours
    # earlier during the radar scan).  Fall back to snap_spot when bar data unavailable.
    if hw_entry_approx and hw_entry_approx > 0:
        entry_spot = hw_entry_approx
    else:
        entry_spot = snap_spot

    iv_source = "massive_eod_backfit" if iv_map else "entry_snapshot_const"
    window_mode = interval_tag == "hold_window_hl"

    hold_bs_iv: Optional[float] = None
    if window_mode:
        if entry_spot > 0 and open_premium > 0:
            dte_hw = max((exp_d - open_d).days, 0)
            t_hw = max(dte_hw / 365.0, 1e-9)
            solved = implied_vol_black_scholes_put(
                entry_spot,
                strike,
                _RISK_FREE_RATE,
                t_hw,
                open_premium,
            )
            if solved is not None:
                hold_bs_iv = float(solved)
                iv_source = "implied_iv_open_fill_hold_window"
            else:
                hold_bs_iv = fallback_iv
                iv_source = "entry_snapshot_iv_hold_window" if iv_map else iv_source
        else:
            hold_bs_iv = fallback_iv
            iv_source = "entry_snapshot_iv_hold_window" if iv_map else iv_source

    def iv_for(day: date) -> float:
        if window_mode:
            return hold_bs_iv if hold_bs_iv is not None else fallback_iv
        return iv_map.get(day, fallback_iv)

    anchor_pnl = 0.0
    if entry_spot > 0:
        iv0 = iv_for(open_d)
        dte0 = max((exp_d - open_d).days, 0)
        t0 = max(dte0 / 365.0, 1e-9)
        mark0 = black_scholes_price(entry_spot, strike, _RISK_FREE_RATE, iv0, t0, "P")
        if mark0 > 0:
            anchor_pnl = short_put_premium_pnl_pct(open_premium, mark0)

    pnl_series: List[float] = [anchor_pnl]
    for bar_d, lo, hi in day_bars:
        iv_day = iv_for(bar_d)
        dte_bar = max((exp_d - bar_d).days, 0)
        t_years = max(dte_bar / 365.0, 1e-9)
        price_lo = black_scholes_price(lo, strike, _RISK_FREE_RATE, iv_day, t_years, "P")
        price_hi = black_scholes_price(hi, strike, _RISK_FREE_RATE, iv_day, t_years, "P")
        if price_lo > 0:
            pnl_series.append(short_put_premium_pnl_pct(open_premium, price_lo))
        if price_hi > 0:
            pnl_series.append(short_put_premium_pnl_pct(open_premium, price_hi))

    data_count = len(pnl_series) - 1  # excludes anchor; counts HL bar points only

    # Invariant pin: actual close P&L is a guaranteed point on the holding curve.
    # Without it, the BS model can report MFE < close_pnl (or MAE > close_pnl) when
    # the intraday IV/spread doesn't fully align with the fill price.
    close_premium = float(pos.get("close_premium") or 0)
    if close_premium > 0:
        pnl_series.append(short_put_premium_pnl_pct(open_premium, close_premium))
    if data_count == 0:
        _LOG.info("intraday_bs: empty pnl series position_id=%s", position_id)
        return

    # --- 4. MAE/MFE ---
    mae_obs, mfe_obs = relative_mae_mfe_from_pnls_chronologic(pnl_series)

    block: Dict[str, Any] = {
        "source": "intraday_bs",
        "model": "daily_hl_bs_eod_iv",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "option_ticker": opt_ticker,
        "bar_count": data_count,          # 2 × trading_days
        "mae_pnl_pct": None if mae_obs is None else round(float(mae_obs), 6),
        "mfe_pnl_pct": None if mfe_obs is None else round(float(mfe_obs), 6),
        "basis": "entry_premium_excursion_pct",
        "iv_source": iv_source,
        "interval": interval_tag,
        "hold_window": {
            "open_date_et": open_d.isoformat(),
            "close_date_et": close_d.isoformat(),
        },
        "fetch_clip": {
            "start_date_et": start_d.isoformat(),
            "end_date_et": end_d_clip.isoformat(),
        },
    }
    if hold_window_fallback:
        block["hold_window_fallback"] = True

    prev = repo.get_open_snapshot(position_id) or {}
    merged = dict(prev) if isinstance(prev, dict) else {}
    merged["intraday_bs"] = block
    repo.save_open_snapshot(position_id, merged)
    _LOG.info(
        "intraday_bs ok position_id=%s ticker=%s bar_count=%s iv_src=%s mae=%s mfe=%s",
        position_id, opt_ticker, data_count, iv_source, mae_obs, mfe_obs,
    )
