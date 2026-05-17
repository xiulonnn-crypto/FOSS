from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.data.provider_yfinance import YFinanceProvider, _prev_business_day


# ---------------------------------------------------------------------------
# _prev_business_day
# ---------------------------------------------------------------------------

def test_prev_business_day_weekday():
    monday = date(2026, 5, 11)  # Monday
    assert _prev_business_day(monday) == monday


def test_prev_business_day_saturday():
    saturday = date(2026, 5, 9)
    friday = date(2026, 5, 8)
    assert _prev_business_day(saturday) == friday


def test_prev_business_day_sunday():
    sunday = date(2026, 5, 10)
    friday = date(2026, 5, 8)
    assert _prev_business_day(sunday) == friday


# ---------------------------------------------------------------------------
# get_option_chain — mock yfinance
# ---------------------------------------------------------------------------

def _make_puts_df(spot=100.0):
    return pd.DataFrame(
        [
            {
                "strike": 90.0,
                "bid": 1.0,
                "ask": 1.2,
                "lastPrice": 1.1,
                "impliedVolatility": 0.30,
                "delta": -0.15,
                "theta": -0.02,
                "vega": 0.05,
                "gamma": 0.01,
                "openInterest": 500,
                "volume": 100,
            },
            {
                "strike": 60.0,  # > 30% from 100 — should be filtered
                "bid": 0.05,
                "ask": 0.10,
                "lastPrice": 0.07,
                "impliedVolatility": 0.20,
                "delta": -0.03,
                "theta": -0.001,
                "vega": 0.01,
                "gamma": 0.001,
                "openInterest": 10,
                "volume": 5,
            },
        ]
    )


@patch("app.data.provider_yfinance.yf.Ticker")
def test_get_option_chain_filters_wide_strikes(mock_ticker_cls):
    exp = date.today() + timedelta(days=35)
    mock_chain = MagicMock()
    mock_chain.puts = _make_puts_df(100.0)

    mock_ticker = MagicMock()
    mock_ticker.option_chain.return_value = mock_chain
    mock_ticker.fast_info.last_price = 100.0
    mock_ticker_cls.return_value = mock_ticker

    provider = YFinanceProvider()
    contracts = provider.get_option_chain("AAPL", exp, right="P")

    strikes = [c.strike for c in contracts]
    assert 90.0 in strikes, "In-range strike should be included"
    assert 60.0 not in strikes, "Wide-strike should be filtered out"


@patch("app.data.provider_yfinance.yf.Ticker")
def test_get_option_chain_anchor_keeps_far_otm_strike(mock_ticker_cls):
    """Deep OTM strikes outside spot±30% must remain fetchable for position marks."""
    exp = date.today() + timedelta(days=35)
    mock_chain = MagicMock()
    mock_chain.puts = _make_puts_df(100.0)

    mock_ticker = MagicMock()
    mock_ticker.option_chain.return_value = mock_chain
    mock_ticker.fast_info.last_price = 100.0
    mock_ticker_cls.return_value = mock_ticker

    provider = YFinanceProvider()
    contracts = provider.get_option_chain("AAPL", exp, right="P", anchor_strike=60.0)

    strikes = [c.strike for c in contracts]
    assert 90.0 in strikes
    assert 60.0 in strikes


@patch("app.data.provider_yfinance.yf.Ticker")
def test_get_option_chain_missing_bid_ask(mock_ticker_cls):
    exp = date.today() + timedelta(days=35)
    df = pd.DataFrame(
        [{"strike": 95.0, "bid": None, "ask": None, "lastPrice": 1.0,
          "impliedVolatility": 0.25, "delta": None, "theta": None,
          "vega": None, "gamma": None, "openInterest": 100, "volume": 20}]
    )
    mock_chain = MagicMock()
    mock_chain.puts = df
    mock_ticker = MagicMock()
    mock_ticker.option_chain.return_value = mock_chain
    mock_ticker.fast_info.last_price = 100.0
    mock_ticker_cls.return_value = mock_ticker

    provider = YFinanceProvider()
    contracts = provider.get_option_chain("AAPL", exp)
    assert len(contracts) == 1
    assert contracts[0].bid is None
    assert contracts[0].ask is None
    assert contracts[0].mid is None


def test_contract_mid_computes():
    from app.core.types import OptionContract
    c = OptionContract(
        symbol="AAPL", expiration=date.today(), strike=150.0, right="P",
        bid=1.0, ask=1.4, last=1.2, iv=0.25,
        delta=-0.15, theta=-0.02, vega=0.05, gamma=0.01,
        open_interest=100, volume=50
    )
    assert abs(c.mid - 1.2) < 1e-6
