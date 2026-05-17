"""OSI option ticker formatting."""

from datetime import date

import pytest

from app.core.option_ticker_osi import format_osi_option_ticker


def test_format_osi_put_spy_720():
    assert format_osi_option_ticker("SPY", date(2024, 12, 20), "P", 720.0) == "O:SPY241220P00720000"


def test_format_osi_call_lowercase_right():
    assert format_osi_option_ticker("iwm", date(2023, 3, 27), "c", 137.0) == "O:IWM230327C00137000"


def test_format_osi_rejects_empty_symbol():
    with pytest.raises(ValueError):
        format_osi_option_ticker("", date(2024, 1, 1), "P", 100.0)
