from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.core.entry_signal import ENTRY_SIGNAL_SCHEMA, build_entry_signal
from app.core.option_pool import evaluate_option_watch


def _pool_row(**overrides):
    row = {
        "id": 12,
        "symbol": "AAPL",
        "expiration": (date.today() + timedelta(days=35)).isoformat(),
        "strike": 155.0,
        "right": "P",
        "bid": 3.0,
        "ask": 3.2,
        "mid": 3.1,
        "spot": 175.0,
        "iv": 0.28,
        "iv_rank": 65.0,
        "delta": -0.15,
        "dte": 35,
        "annualized_roi": 0.21,
        "spread_pct": 0.0645,
        "breakeven": 151.9,
        "margin_buffer": 0.1143,
        "score": 0.82,
        "open_interest": 500,
        "quality_grade": "A",
        "quality_score": 95,
        "quality_flags": ["provider_delayed"],
        "quote_age_seconds": 900,
        "greeks_source": "provider",
        "iv_rank_source": "rv_proxy",
        "status": "ACTIVE",
        "last_scan_run_id": 7,
        "latest_candidate_id": 88,
    }
    row.update(overrides)
    return row


def test_build_entry_signal_openable_with_explainable_reasons():
    signal = build_entry_signal(
        _pool_row(),
        now=datetime(2026, 5, 16, 2, 0, tzinfo=timezone.utc),
        today=date.today(),
    )

    assert signal["schema"] == ENTRY_SIGNAL_SCHEMA
    assert signal["status"] == "OPENABLE"
    assert signal["decision_score"] >= 70
    assert signal["source"]["option_pool_id"] == 12
    assert signal["source"]["latest_candidate_id"] == 88
    assert signal["metrics"]["return"]["max_profit"] == 310.0
    assert any(r["code"] == "roi_pass" for r in signal["reasons"])
    assert signal["blockers"] == []


def test_build_entry_signal_waits_when_premium_or_roi_not_met():
    signal = build_entry_signal(
        _pool_row(mid=2.0, annualized_roi=0.08),
        watch_row={"target_premium": 3.0},
        today=date.today(),
    )

    assert signal["status"] == "WAIT"
    codes = {r["code"] for r in signal["reasons"]}
    assert "roi_below_target" in codes
    assert "target_premium_not_met" in codes
    assert "建议等待" in signal["summary"]


def test_build_entry_signal_rejects_blocked_or_low_quality_rows():
    signal = build_entry_signal(
        _pool_row(status="BLOCKED", quality_grade="C", quality_flags=["wide_spread"]),
        today=date.today(),
    )

    assert signal["status"] == "REJECT"
    assert {r["code"] for r in signal["blockers"]} >= {"pool_blocked", "quality_c"}


def test_entry_signal_can_gate_watch_readiness():
    pool = _pool_row(entry_signal_status="WAIT")
    watch = {"status": "WATCHING", "target_premium": 1.0}

    signal = evaluate_option_watch(pool, watch, date.today())

    assert signal["status"] == "WATCHING"
    assert signal["reason"] == "entry_signal_wait"
