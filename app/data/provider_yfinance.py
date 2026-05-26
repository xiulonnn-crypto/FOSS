from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

import pandas as pd
import yfinance as yf

from app.core.types import OptionContract, Quote
from app.data.provider_base import MarketDataProvider

log = logging.getLogger(__name__)

# strike range filter: spot ± this fraction (screener/radar payload size)
_STRIKE_RANGE = 0.30
# When ``anchor_strike`` is set (e.g. marking an open position), keep strikes
# within this absolute band around the anchor so far OTM CSPs are not dropped.
_ANCHOR_BAND_ABS_MIN = 15.0
_ANCHOR_BAND_FRAC = 0.03  # 3% of anchor strike, e.g. ±$14.4 @ K=480 → ±$15 floor
# max retries on yfinance throttle
_MAX_RETRIES = 3


def _prev_business_day(d: date) -> date:
    """Return d if weekday, else the most recent Friday."""
    dt = datetime.combine(d, datetime.min.time())
    while dt.weekday() >= 5:  # 5=Sat, 6=Sun
        dt -= timedelta(days=1)
    return dt.date()


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"
    realtime = False  # ~15 min delay

    def get_quote(self, symbol: str) -> Quote:
        ticker = yf.Ticker(symbol)
        for attempt in range(_MAX_RETRIES):
            try:
                info = ticker.fast_info
                spot = float(info.last_price or info.previous_close)
                return Quote(symbol=symbol, spot=spot, asof=datetime.utcnow())
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise RuntimeError(f"get_quote({symbol}) failed: {exc}") from exc
        raise RuntimeError("unreachable")

    def get_expirations(self, symbol: str) -> List[date]:
        ticker = yf.Ticker(symbol)
        raw = ticker.options or []
        result = []
        for s in raw:
            try:
                result.append(datetime.strptime(s, "%Y-%m-%d").date())
            except ValueError:
                pass
        return sorted(result)

    def get_option_chain(
        self,
        symbol: str,
        expiration: date,
        right: str = "P",
        anchor_strike: Optional[float] = None,
        *,
        underlying_spot: Optional[float] = None,
    ) -> List[OptionContract]:
        exp_str = expiration.strftime("%Y-%m-%d")
        for attempt in range(_MAX_RETRIES):
            try:
                chain = yf.Ticker(symbol).option_chain(exp_str)
                break
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                else:
                    log.warning("option_chain(%s, %s) failed: %s", symbol, exp_str, exc)
                    return []

        df = chain.puts if right == "P" else chain.calls
        if df is None or df.empty:
            return []

        # get spot for range filter (reuse quote when caller already has it)
        spot = underlying_spot
        if spot is None:
            try:
                spot = float(self.get_quote(symbol).spot)
            except Exception:
                spot = None

        today = date.today()
        dte = (expiration - today).days
        contracts = []
        for _, row in df.iterrows():
            strike = float(row.get("strike", 0) or 0)
            if strike <= 0:
                continue
            # strike range filter; optional anchor includes position strike far from spot
            near_spot = spot is None or abs(strike - spot) / spot <= _STRIKE_RANGE
            near_anchor = False
            if anchor_strike is not None:
                band = max(_ANCHOR_BAND_ABS_MIN, anchor_strike * _ANCHOR_BAND_FRAC)
                near_anchor = abs(strike - anchor_strike) <= band
            if not (near_spot or near_anchor):
                continue
            # DTE range (broad pre-filter: 1–120 days)
            if dte < 1 or dte > 120:
                continue

            bid = _float_or_none(row.get("bid"))
            ask = _float_or_none(row.get("ask"))
            iv = _float_or_none(row.get("impliedVolatility"))
            delta = _float_or_none(row.get("delta"))
            theta = _float_or_none(row.get("theta"))
            vega = _float_or_none(row.get("vega"))
            gamma = _float_or_none(row.get("gamma"))
            oi = _int_or_none(row.get("openInterest"))
            vol = _int_or_none(row.get("volume"))

            contracts.append(
                OptionContract(
                    symbol=symbol,
                    expiration=expiration,
                    strike=strike,
                    right=right,
                    bid=bid,
                    ask=ask,
                    last=_float_or_none(row.get("lastPrice")),
                    iv=iv,
                    delta=delta,
                    theta=theta,
                    vega=vega,
                    gamma=gamma,
                    open_interest=oi,
                    volume=vol,
                    quote_age_seconds=900,  # ~15 min delay
                )
            )
        return contracts

    def get_historical_close(self, symbol: str, day: date) -> Optional[float]:
        actual_day = _prev_business_day(day)
        ticker = yf.Ticker(symbol)
        end = actual_day + timedelta(days=1)
        df = ticker.history(start=str(actual_day), end=str(end), auto_adjust=True)
        if df.empty:
            # try fetching a slightly wider window
            start_fallback = actual_day - timedelta(days=5)
            df = ticker.history(start=str(start_fallback), end=str(end), auto_adjust=True)
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])

    def get_historical_closes(self, symbol: str, days: int = 400) -> List[float]:
        ticker = yf.Ticker(symbol)
        end = date.today() + timedelta(days=1)
        start = date.today() - timedelta(days=days + 20)
        df = ticker.history(start=str(start), end=str(end), auto_adjust=True)
        if df.empty or "Close" not in df:
            return []
        closes = []
        for value in df["Close"].dropna().tolist():
            try:
                closes.append(float(value))
            except (TypeError, ValueError):
                continue
        return closes[-days:]

    def get_iv_history(self, symbol: str, days: int = 252) -> List[Tuple[date, float]]:
        """Return (date, log_return_std_annualised) as RV proxy for IV Rank."""
        ticker = yf.Ticker(symbol)
        end = date.today()
        start = end - timedelta(days=days + 60)  # extra buffer
        df = ticker.history(start=str(start), end=str(end), auto_adjust=True)
        if df.empty or len(df) < 10:
            return []
        closes = df["Close"].dropna()
        log_returns = closes.pct_change().dropna()
        # rolling 21-day window annualised
        rv_series = log_returns.rolling(21).std() * (252 ** 0.5)
        rv_series = rv_series.dropna()
        result = []
        for ts, rv in rv_series.items():
            try:
                d = ts.date() if hasattr(ts, "date") else ts
                result.append((d, float(rv)))
            except Exception:
                pass
        return result[-days:]

    def get_next_earnings(self, symbol: str) -> Optional[date]:
        try:
            cal = yf.Ticker(symbol).calendar
            if cal is None:
                return None
            # calendar can be a dict or DataFrame depending on yfinance version
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed is None:
                    return None
                if hasattr(ed, "__iter__") and not isinstance(ed, str):
                    ed = list(ed)[0]
                if hasattr(ed, "date"):
                    return ed.date()
                return None
            if isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.columns:
                val = cal["Earnings Date"].iloc[0]
                if hasattr(val, "date"):
                    return val.date()
            return None
        except Exception:
            return None


def _float_or_none(v) -> Optional[float]:
    try:
        f = float(v)
        return f if pd.notna(f) else None
    except (TypeError, ValueError):
        return None


def _int_or_none(v) -> Optional[int]:
    try:
        f = float(v)
        return int(f) if pd.notna(f) else None
    except (TypeError, ValueError):
        return None
