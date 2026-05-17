from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import app.core.position_mark as position_mark_mod
from app.core.greeks import black_scholes_price
from app.core.position_mark import mark_short_put_position
from app.core.types import OptionContract, Quote


def test_mark_short_put_uses_black_scholes_theory(monkeypatch):
    frozen = date(2026, 5, 10)
    monkeypatch.setattr(position_mark_mod, "et_calendar_today", lambda: frozen)

    provider = MagicMock()
    provider.get_quote.return_value = Quote(
        symbol="AAPL",
        spot=170.0,
        asof=datetime(2026, 5, 10, 16, 0, tzinfo=timezone.utc),
    )
    exp = date(2026, 6, 20)
    provider.get_option_chain.return_value = [
        OptionContract(
            symbol="AAPL",
            expiration=exp,
            strike=150.0,
            right="P",
            bid=0.10,
            ask=3.10,
            last=None,
            iv=0.25,
            delta=-0.15,
            theta=-0.01,
            vega=0.02,
            gamma=0.001,
            open_interest=100,
            volume=50,
        )
    ]
    pos = {
        "symbol": "AAPL",
        "expiration": "2026-06-20",
        "strike": 150.0,
        "contracts": 2,
        "open_premium": 2.0,
    }
    m = mark_short_put_position(pos, provider, 0.045)
    assert m["spot"] == 170.0
    assert provider.get_option_chain.call_args.kwargs.get("underlying_spot") == 170.0
    dte = (exp - frozen).days
    t_years = max(dte / 365.0, 1e-6)
    expected_mid = black_scholes_price(170.0, 150.0, 0.045, 0.25, t_years, "P")
    assert abs(m["option_mid"] - expected_mid) < 1e-5
    assert m["mark_basis"] == "bs"
    assert abs(m["option_bs"] - expected_mid) < 1e-5
    pnl_expected = round(1.0 - expected_mid / 2.0, 4)
    assert m["pnl_pct"] == pnl_expected
    assert m["unrealized_pnl_usd"] == round((2.0 - expected_mid) * 100 * 2, 2)
    assert "quote_error" not in m
    assert m["spot_asof"].startswith("2026-05-10T12:00:00")
    assert "-04:00" in m["spot_asof"]


def test_mark_short_put_bs_prices_position_strike_not_only_chain_row(monkeypatch):
    """Nearest chain row IV applies to BS at the held strike (may differ from row strike)."""
    frozen = date(2026, 5, 10)
    monkeypatch.setattr(position_mark_mod, "et_calendar_today", lambda: frozen)

    provider = MagicMock()
    provider.get_quote.return_value = Quote(
        symbol="AAPL",
        spot=170.0,
        asof=datetime(2026, 5, 10, 16, 0, tzinfo=timezone.utc),
    )
    exp = date(2026, 6, 20)
    provider.get_option_chain.return_value = [
        OptionContract(
            symbol="AAPL",
            expiration=exp,
            strike=150.0,
            right="P",
            bid=0.10,
            ask=3.10,
            last=None,
            iv=0.25,
            delta=-0.15,
            theta=-0.01,
            vega=0.02,
            gamma=0.001,
            open_interest=100,
            volume=50,
        )
    ]
    pos = {
        "symbol": "AAPL",
        "expiration": "2026-06-20",
        "strike": 151.0,
        "contracts": 2,
        "open_premium": 2.0,
    }
    m = mark_short_put_position(pos, provider, 0.045)
    dte = (exp - frozen).days
    t_years = max(dte / 365.0, 1e-6)
    expected_mid = black_scholes_price(170.0, 151.0, 0.045, 0.25, t_years, "P")
    assert abs(m["option_mid"] - expected_mid) < 1e-5
    assert m["mark_basis"] == "bs"


def test_mark_prefers_tight_chain_mid_over_bs(monkeypatch):
    frozen = date(2026, 5, 10)
    monkeypatch.setattr(position_mark_mod, "et_calendar_today", lambda: frozen)

    provider = MagicMock()
    provider.get_quote.return_value = Quote(
        symbol="AAPL",
        spot=170.0,
        asof=datetime(2026, 5, 10, 16, 0, tzinfo=timezone.utc),
    )
    exp = date(2026, 6, 20)
    provider.get_option_chain.return_value = [
        OptionContract(
            symbol="AAPL",
            expiration=exp,
            strike=150.0,
            right="P",
            bid=19.0,
            ask=23.0,
            last=None,
            iv=0.25,
            delta=-0.15,
            theta=-0.01,
            vega=0.02,
            gamma=0.001,
            open_interest=100,
            volume=50,
        )
    ]
    pos = {
        "symbol": "AAPL",
        "expiration": "2026-06-20",
        "strike": 150.0,
        "contracts": 1,
        "open_premium": 22.0,
    }
    m = mark_short_put_position(pos, provider, 0.045)
    assert m["option_mid"] == 21.0  # mid
    assert m["mark_basis"] == "mid"
    assert m["option_bs"] is not None
    assert abs(m["option_bs"] - 21.0) > 0.01  # BS differs from chosen mid


def test_quality_chain_mid_helper():
    from app.core.position_mark import _quality_chain_mid

    c = OptionContract(
        symbol="X",
        expiration=date.today(),
        strike=100.0,
        right="P",
        bid=19.0,
        ask=23.0,
        last=None,
        iv=0.3,
        delta=None,
        theta=None,
        vega=None,
        gamma=None,
        open_interest=1,
        volume=1,
    )
    assert _quality_chain_mid(c) == 21.0


def test_mark_short_put_quote_error():
    provider = MagicMock()
    provider.get_quote.side_effect = RuntimeError("network")
    pos = {"symbol": "X", "expiration": "2026-06-20", "strike": 1.0, "contracts": 1, "open_premium": 1.0}
    m = mark_short_put_position(pos, provider)
    assert "quote_error" in m
    assert "spot" not in m


def test_mark_short_put_prefetched_skips_quote_fetch():
    pq = Quote(symbol="AAPL", spot=99.0, asof=datetime(2026, 5, 10, tzinfo=timezone.utc))
    provider = MagicMock()
    provider.get_option_chain.return_value = []
    pos = {"symbol": "AAPL", "expiration": "2026-06-20", "strike": 150.0, "contracts": 1, "open_premium": 2.0}
    m = mark_short_put_position(pos, provider, prefetched_quote=pq)
    assert m["spot"] == 99.0
    provider.get_quote.assert_not_called()
