from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.exit_signal import EXIT_SIGNAL_SCHEMA, build_exit_signal


NOW = datetime(2026, 5, 16, 15, 30, tzinfo=timezone.utc)

SETTINGS = {
    "exits": {
        "take_profit_pct": 0.50,
        "take_profit_strong_pct": 0.75,
        "time_warning_dte": 14,
        "time_danger_dte": 7,
        "danger_distance_pct": 0.03,
        "delta_breach_abs": 0.40,
        "fast_profit_days": 5,
        "fast_profit_pct": 0.50,
        "loss_pnl_pct_danger": -0.50,
        "expiry_hold_max_mid": 0.05,
        "expiry_hold_min_margin_buffer": 0.08,
    }
}

REASON_KEYS = {
    "code",
    "dimension",
    "severity",
    "message",
    "current",
    "threshold",
    "passed",
}


def _position(
    *,
    dte: int = 35,
    strike: float = 150.0,
    open_premium: float = 2.0,
    open_days: int = 20,
):
    return {
        "id": 10,
        "symbol": "AAPL",
        "expiration": (NOW.date() + timedelta(days=dte)).isoformat(),
        "strike": strike,
        "contracts": 1,
        "open_at": (NOW - timedelta(days=open_days)).isoformat(),
        "open_premium": open_premium,
        "state": "OPEN",
    }


def _mark(
    *,
    spot: float = 175.0,
    strike: float = 150.0,
    current_mid: float = 1.8,
    delta: float = -0.15,
    open_premium: float = 2.0,
):
    return {
        "spot": spot,
        "option_mid": current_mid,
        "delta": delta,
        "margin_buffer": (spot - strike) / spot,
        "pnl_pct": 1.0 - (current_mid / open_premium),
    }


def _codes(signal):
    return {r["code"] for r in signal["reasons"]}


def _assert_reason_shape(signal):
    assert signal["reasons"]
    for reason in signal["reasons"]:
        assert set(reason) == REASON_KEYS
        assert reason["severity"] in {"info", "warn", "danger"}


def test_fast_profit_accelerates_take_profit_with_new_close_reason():
    signal = build_exit_signal(
        _position(open_days=2),
        _mark(current_mid=0.9),
        SETTINGS,
        now=NOW,
    )

    assert signal["schema"] == EXIT_SIGNAL_SCHEMA
    assert signal["action"] == "ACCELERATE_TAKE_PROFIT"
    assert signal["severity"] == "warn"
    assert signal["suggested_close_reason"] == "take_profit_fast"
    assert signal["legacy_signals"] == ["take_profit_50"]
    assert "take_profit_fast" in _codes(signal)
    _assert_reason_shape(signal)


@pytest.mark.parametrize(
    ("dte", "expected_reason", "expected_severity"),
    [(7, "time_7d", "danger"), (14, "time_14d", "warn")],
)
def test_dte_windows_generate_time_exit(dte, expected_reason, expected_severity):
    signal = build_exit_signal(
        _position(dte=dte),
        _mark(current_mid=1.8),
        SETTINGS,
        now=NOW,
    )

    assert signal["action"] == "TIME_EXIT"
    assert signal["severity"] == expected_severity
    assert signal["suggested_close_reason"] == expected_reason
    assert signal["legacy_signals"] == [expected_reason]
    assert expected_reason in _codes(signal)
    _assert_reason_shape(signal)


@pytest.mark.parametrize(
    ("mark", "expected_code", "expected_close_reason"),
    [
        (_mark(delta=-0.45), "delta_breach", "delta_breach"),
        (_mark(spot=149.0, strike=150.0), "spot_below_strike", "danger_3pct"),
        (_mark(current_mid=3.2), "loss_breach", "loss_breach"),
    ],
)
def test_defend_priority_for_delta_spot_and_loss(mark, expected_code, expected_close_reason):
    signal = build_exit_signal(_position(), mark, SETTINGS, now=NOW)

    assert signal["action"] == "DEFEND"
    assert signal["severity"] == "danger"
    assert signal["suggested_close_reason"] == expected_close_reason
    assert expected_code in _codes(signal)
    _assert_reason_shape(signal)


def test_low_value_safe_near_expiry_holds_to_expiry():
    signal = build_exit_signal(
        _position(dte=5),
        _mark(current_mid=0.03),
        SETTINGS,
        now=NOW,
    )

    assert signal["action"] == "HOLD_TO_EXPIRY"
    assert signal["severity"] == "info"
    assert signal["suggested_close_reason"] is None
    assert "expiry_hold_candidate" in _codes(signal)
    assert "time_7d" in signal["legacy_signals"]
    _assert_reason_shape(signal)


def test_normal_take_profit_uses_legacy_close_reason():
    signal = build_exit_signal(
        _position(open_days=20),
        _mark(current_mid=0.9),
        SETTINGS,
        now=NOW,
    )

    assert signal["action"] == "TAKE_PROFIT"
    assert signal["suggested_close_reason"] == "take_profit_50"
    assert signal["legacy_signals"] == ["take_profit_50"]
    assert "take_profit_50" in _codes(signal)
    _assert_reason_shape(signal)


@pytest.mark.parametrize(
    "mark",
    [
        {"quote_error": "provider timeout"},
        {
            "chain_error": "chain timeout",
            "spot": 175.0,
            "option_mid": 1.8,
            "pnl_pct": 0.1,
            "margin_buffer": 0.14,
        },
        {"spot": 175.0, "delta": -0.15, "pnl_pct": 0.1, "margin_buffer": 0.14},
    ],
)
def test_missing_or_failed_mark_is_unknown_without_legacy_signals(mark):
    signal = build_exit_signal(_position(), mark, SETTINGS, now=NOW)

    assert signal["action"] == "UNKNOWN"
    assert signal["severity"] == "warn"
    assert signal["legacy_signals"] == []
    _assert_reason_shape(signal)
