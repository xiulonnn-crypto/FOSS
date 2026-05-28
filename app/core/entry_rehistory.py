"""Rebuild closed-position review data from open_at: BS Greeks + synthetic daily radar."""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.greeks import (
    black_scholes_delta,
    black_scholes_price,
    fill_greeks,
    implied_vol_black_scholes_put,
)
from app.core.massive_closed_enrichment import enrich_closed_position_open_snapshot_massive
from app.core.pnl_excursion_intraday import enrich_closed_position_intraday_bs
from app.core.open_snapshot import (
    build_open_snapshot_dict,
    closes_through_entry,
    position_open_datetime,
    _entry_minute_close,
)
from app.core.time_et import APP_TZ, parse_instant_utc
from app.core.types import OptionContract
from app.db.repo import Repo

_LOG = logging.getLogger(__name__)

_CLOSED_FOR_RECALC = frozenset({"CLOSED_EARLY", "EXPIRED_OTM", "ASSIGNED"})


def _sanitize_float(v: Optional[float], ndigits: int) -> Optional[float]:
    if v is None:
        return None
    x = float(v)
    if math.isnan(x):
        return None
    return round(x, ndigits)


def daily_underlying_closes(symbol: str, start_d: date, end_incl: date) -> List[Tuple[date, float]]:
    """Sorted ascending (calendar date, adj close)."""
    try:
        import yfinance as yf
    except ImportError:
        return []

    sym = (symbol or "").upper().strip()
    if not sym:
        return []
    end_excl = end_incl + timedelta(days=1)
    warmup = start_d - timedelta(days=21)
    ticker = yf.Ticker(sym)
    hist = ticker.history(
        start=warmup.isoformat(),
        end=end_excl.isoformat(),
        interval="1d",
        auto_adjust=True,
    )
    if hist.empty:
        return []
    out: List[Tuple[date, float]] = []
    for ts, row in hist.iterrows():
        bar_d = ts.date() if hasattr(ts, "date") else ts
        if start_d <= bar_d <= end_incl:
            out.append((bar_d, float(row["Close"])))
    out.sort(key=lambda x: x[0])
    return out


def spot_open_estimate(symbol: str, pos: Dict[str, Any], open_d: date) -> Optional[float]:
    entry_dt = position_open_datetime(pos)
    if entry_dt is None:
        return None
    closes = closes_through_entry(symbol, entry_dt)
    if closes:
        return float(closes[-1])
    hist = daily_underlying_closes(symbol, open_d, open_d)
    if not hist:
        return None
    return float(hist[-1][1])


def _merge_snapshot_layers(
    prev: Dict[str, Any],
    technicals: Dict[str, Any],
    entry: Dict[str, Any],
) -> Dict[str, Any]:
    out = dict(prev)
    for src in (technicals, entry):
        for k, v in src.items():
            if v is not None:
                out[k] = v
    return out


def recalculate_closed_position_insights(
    repo: Repo,
    position_id: int,
    *,
    risk_free_rate: float,
) -> Dict[str, Any]:
    """
    Closed short-puts only:

    - Re-merge ``open_snapshot`` with BS entry Greeks (IV implied from ``open_premium``,
      spot ~ last daily close ≤ entry bar from yfinance RSI pipeline when available).
    - **Deletes** existing ``radar_snapshots`` rows, then inserts a **synthetic daily**
      curve (underlying close per session, BS mark with fixed IV). This restores
      attribution ``spot_close`` and MAE/MFE where live radar never ran.

    Approximation: IV held constant across the replay; excludes intraday path.
    """
    pos = repo.get_position(position_id)
    if not pos:
        raise ValueError("position not found")
    st = str(pos.get("state") or "")
    if st not in _CLOSED_FOR_RECALC:
        raise ValueError("only closed positions can be recalculated")

    close_dt = parse_instant_utc(pos.get("close_at"))
    if close_dt is None:
        raise ValueError("missing or invalid close_at")

    open_dt = parse_instant_utc(pos.get("open_at"))
    if open_dt is None:
        raise ValueError("missing or invalid open_at")
    if close_dt < open_dt:
        raise ValueError(
            "close_at 早于 open_at（按绝对时间比较）。请在复盘「编辑」中核对开仓/平仓时间与时区；"
            f"open_at={open_dt.isoformat()}, close_at={close_dt.isoformat()}"
        )

    symbol = (pos.get("symbol") or "").upper().strip()
    exp_raw = str(pos.get("expiration") or "").strip()[:10]
    open_d = open_dt.astimezone(APP_TZ).date()
    try:
        exp_d = date.fromisoformat(exp_raw)
    except ValueError as exc:
        raise ValueError("invalid expiration") from exc

    strike = float(pos.get("strike") or 0)
    open_premium = float(pos.get("open_premium") or 0)
    if strike <= 0 or open_premium <= 0:
        raise ValueError("strike and open_premium must be positive")

    # Same calendar day as expiration is valid (0-DTE / expiry-day entry); only
    # strictly-after-expiry dates are inconsistent with a live option position.
    if open_d > exp_d:
        raise ValueError("open date after expiration")

    dte_entry = max((exp_d - open_d).days, 1)
    t_entry = max(dte_entry / 365.0, 1e-6)

    spot0 = spot_open_estimate(symbol, pos, open_d)
    if spot0 is None or spot0 <= 0:
        raise ValueError("could not resolve underlying spot at entry")

    iv = implied_vol_black_scholes_put(spot0, strike, risk_free_rate, t_entry, open_premium)
    if iv is None:
        raise ValueError("could not imply IV from open_premium (check strike/premium)")

    base_contract = OptionContract(
        symbol=symbol,
        expiration=exp_d,
        strike=strike,
        right="P",
        bid=None,
        ask=None,
        last=None,
        iv=float(iv),
        delta=None,
        theta=None,
        vega=None,
        gamma=None,
        open_interest=None,
        volume=None,
    )
    filled = fill_greeks(base_contract, spot0, risk_free_rate, valuation_date=open_d)

    annualized_roi = (open_premium / strike) * (365.0 / dte_entry)

    entry_block: Dict[str, Any] = {
        "spot": _sanitize_float(spot0, 6),
        "iv": round(float(iv), 6),
        "delta": _sanitize_float(float(filled.delta) if filled.delta is not None else None, 8),
        "theta": _sanitize_float(float(filled.theta) if filled.theta is not None else None, 8),
        "vega": _sanitize_float(float(filled.vega) if filled.vega is not None else None, 8),
        "dte": dte_entry,
        "annualized_roi": round(float(annualized_roi), 6),
        "replay_model": "bs_daily_close_constant_iv",
    }

    prev_snap = repo.get_open_snapshot(position_id) or {}
    tech = build_open_snapshot_dict(repo, pos, None)
    merged = _merge_snapshot_layers(prev_snap, tech, entry_block)
    try:
        from app.core.review_backfill import backfill_diagnostic_fields
        merged = backfill_diagnostic_fields(repo, pos, merged, repo.get_settings() or {})
    except Exception as exc:
        _LOG.warning("entry_recalc: backfill failed (non-fatal) position_id=%s: %s", position_id, exc)
    repo.save_open_snapshot(position_id, merged)

    close_d = close_dt.astimezone(APP_TZ).date()
    end_d = min(close_d, exp_d)

    repo.delete_radar_snapshots_for_position(position_id)

    bars = daily_underlying_closes(symbol, open_d, end_d)
    if not bars:
        raise ValueError("no underlying history for replay window")

    inserted = 0
    for bar_d, spot_d in bars:
        if bar_d < open_d or bar_d > end_d:
            continue
        dte_d = max((exp_d - bar_d).days, 0)
        t_y = max(dte_d / 365.0, 1e-9)
        if bar_d >= exp_d:
            mid_d = max(float(strike) - float(spot_d), 0.0)
            delta_d = 0.0 if mid_d <= 1e-9 else -1.0
        else:
            mid_d = black_scholes_price(spot_d, strike, risk_free_rate, float(iv), t_y, "P")
            if math.isnan(mid_d) or mid_d <= 0:
                continue
            delta_d = black_scholes_delta(spot_d, strike, risk_free_rate, float(iv), t_y, "P")
        pnl_pct = 1.0 - (mid_d / open_premium) if open_premium > 0 else 0.0
        mb = (spot_d - strike) / spot_d if spot_d > 0 else 0.0
        taken = datetime.combine(bar_d, time(16, 0), tzinfo=APP_TZ).astimezone(timezone.utc)
        repo.insert_radar_snapshot(
            {
                "position_id": position_id,
                "taken_at": taken.isoformat(),
                "spot": round(float(spot_d), 6),
                "current_mid": round(float(mid_d), 6),
                "pnl_pct": round(float(pnl_pct), 6),
                "delta": None if math.isnan(delta_d) else round(float(delta_d), 6),
                "margin_buffer": round(float(mb), 6),
                "signals": json.dumps(["synthetic_replay"]),
            }
        )
        inserted += 1

    if inserted == 0:
        _LOG.warning("entry_recalc zero radar rows position_id=%s", position_id)

    try:
        enrich_closed_position_open_snapshot_massive(repo, position_id)
    except Exception as exc:
        _LOG.warning("entry_recalc: massive enrich failed (non-fatal) position_id=%s: %s", position_id, exc)
    try:
        enrich_closed_position_intraday_bs(repo, position_id)
    except Exception as exc:
        _LOG.warning("entry_recalc: intraday_bs enrich failed (non-fatal) position_id=%s: %s", position_id, exc)

    # Build close_snapshot (出场环境快照) from BS replay data.
    close_snapshot_built = False
    try:
        close_snapshot_built = _build_and_save_close_snapshot(
            repo=repo,
            pos=pos,
            position_id=position_id,
            close_dt=close_dt,
            close_d=close_d,
            strike=strike,
            open_premium=open_premium,
            exp_d=exp_d,
            iv=float(iv),
            risk_free_rate=risk_free_rate,
        )
    except Exception as exc:
        _LOG.warning("entry_recalc: close_snapshot build failed (non-fatal) position_id=%s: %s", position_id, exc)

    return {
        "ok": True,
        "position_id": position_id,
        "open_snapshot_keys": sorted(merged.keys()),
        "radar_rows_inserted": inserted,
        "implied_iv": float(iv),
        "close_snapshot_built": close_snapshot_built,
    }


def _build_and_save_close_snapshot(
    *,
    repo: Repo,
    pos: Dict[str, Any],
    position_id: int,
    close_dt: datetime,
    close_d: date,
    strike: float,
    open_premium: float,
    exp_d: date,
    iv: float,
    risk_free_rate: float,
) -> bool:
    """
    Reconstruct the exit environment snapshot using the BS replay model and save it
    via ``repo.save_position_close_snapshot``.  Returns True when saved.
    """
    # Resolve underlying spot at the close moment (intraday bar preferred).
    close_spot: Optional[float] = _entry_minute_close(pos.get("symbol", ""), close_dt)
    if close_spot is None or close_spot <= 0:
        # Fall back to daily close of close_date via the same bars used for replay.
        bars = daily_underlying_closes(str(pos.get("symbol", "")), close_d, close_d)
        if bars:
            close_spot = float(bars[-1][1])

    close_premium_val = float(pos.get("close_premium") or 0)
    realized_pnl = float(pos.get("realized_pnl") or 0)
    close_reason = str(pos.get("close_reason") or "manual")
    close_notes = pos.get("notes")

    mark: Dict[str, Any] = {
        "mark_basis": "bs_daily_close_constant_iv_replay",
        "option_mid": close_premium_val if close_premium_val > 0 else None,
        "current_mid": close_premium_val if close_premium_val > 0 else None,
    }
    if close_spot and close_spot > 0:
        dte_close = max((exp_d - close_d).days, 0)
        t_close = max(dte_close / 365.0, 1e-6)
        delta_close = black_scholes_delta(close_spot, strike, risk_free_rate, iv, t_close, "P")
        margin_buf = (close_spot - strike) / close_spot
        pnl_pct = 1.0 - (close_premium_val / open_premium) if open_premium > 0 and close_premium_val >= 0 else None
        mark.update({
            "spot": round(float(close_spot), 6),
            "iv": round(float(iv), 6),
            "delta": None if math.isnan(delta_close) else round(float(delta_close), 6),
            "margin_buffer": round(float(margin_buf), 6),
            "pnl_pct": round(float(pnl_pct), 6) if pnl_pct is not None else None,
        })

    close_snapshot = {
        "schema": "position_close_snapshot_v1",
        "closed_at": close_dt.isoformat(),
        "close_premium": close_premium_val if close_premium_val > 0 else None,
        "selected_close_reason": close_reason,
        "close_notes": close_notes,
        "realized_pnl": realized_pnl,
        "exit_signal_id": None,
        "exit_signal": None,
        "mark": mark,
        "replay_model": "bs_daily_close_constant_iv",
    }
    repo.save_position_close_snapshot(position_id, close_snapshot)
    return True
