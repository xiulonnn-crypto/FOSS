from __future__ import annotations

from datetime import date, datetime, timedelta

from app.core.option_pool import (
    build_option_pool_row,
    evaluate_option_watch,
    next_option_pool_status,
)


TODAY = date(2026, 5, 16)
NOW = datetime(2026, 5, 16, 9, 30)


def _candidate(**overrides):
    row = {
        "id": 42,
        "symbol": " aapl ",
        "expiration": "2026-06-20",
        "strike": "150",
        "bid": "1.5",
        "ask": 1.7,
        "mid": 1.6,
        "spot": 175.0,
        "iv": 0.28,
        "iv_rank": 63.0,
        "delta": -0.15,
        "dte": 35,
        "annualized_roi": 0.22,
        "spread_pct": 0.08,
        "margin_buffer": 0.14,
        "score": 0.78,
        "quality_grade": "A",
        "quality_score": 95,
        "quality_flags": ["provider_delayed"],
        "quote_age_seconds": 900,
        "greeks_source": "provider",
        "iv_rank_source": "rv_proxy",
    }
    row.update(overrides)
    return row


def test_build_option_pool_row_normalizes_candidate_to_new_put_row():
    row = build_option_pool_row(_candidate(), scan_run_id=7, now=NOW)

    assert row["symbol"] == "AAPL"
    assert row["expiration"] == "2026-06-20"
    assert row["strike"] == 150.0
    assert row["right"] == "P"
    assert row["status"] == "NEW"
    assert row["last_scan_run_id"] == 7
    assert row["latest_candidate_id"] == 42
    assert row["missed_scan_count"] == 0
    assert row["first_seen_at"] == NOW.isoformat()
    assert row["last_seen_at"] == NOW.isoformat()
    assert row["quality_flags"] == ["provider_delayed"]


def test_build_option_pool_row_blocks_quality_c_and_allows_partial_metrics():
    row = build_option_pool_row(
        _candidate(
            id=None,
            bid=None,
            ask=None,
            mid=None,
            quality_grade="C",
            quality_flags='["wide_spread"]',
            reasons=["delta_missing"],
        ),
        scan_run_id=8,
        now=NOW,
    )

    assert row["status"] == "BLOCKED"
    assert row["bid"] is None
    assert row["ask"] is None
    assert row["latest_candidate_id"] is None
    assert row["quality_flags"] == ["wide_spread", "delta_missing"]


def test_build_option_pool_row_status_blocked_does_not_pollute_quality_flags():
    row = build_option_pool_row(
        _candidate(status="BLOCKED", quality_grade="B", quality_flags=[]),
        scan_run_id=8,
        now=NOW,
    )

    assert row["status"] == "BLOCKED"
    assert row["quality_flags"] == []


def test_next_option_pool_status_covers_new_active_stale_expired_lifecycle():
    base = _candidate(status=None, quality_grade="A", expiration="2026-06-20")
    assert next_option_pool_status(base, seen_this_scan=True, today=TODAY) == "NEW"

    new_row = _candidate(status="NEW", quality_grade="A", missed_scan_count=0)
    assert next_option_pool_status(new_row, seen_this_scan=True, today=TODAY) == "ACTIVE"
    assert next_option_pool_status(new_row, seen_this_scan=False, today=TODAY) == "NEW"

    missed_once = _candidate(status="ACTIVE", quality_grade="A", missed_scan_count=1)
    assert next_option_pool_status(missed_once, seen_this_scan=False, today=TODAY) == "ACTIVE"

    missed_twice = _candidate(status="ACTIVE", quality_grade="A", missed_scan_count=2)
    assert next_option_pool_status(missed_twice, seen_this_scan=False, today=TODAY) == "STALE"

    expired = _candidate(status="ACTIVE", quality_grade="A", expiration=TODAY - timedelta(days=1))
    assert next_option_pool_status(expired, seen_this_scan=True, today=TODAY) == "EXPIRED"


def test_next_option_pool_status_blocks_quality_c_or_blocker_flags():
    quality_c = _candidate(status="ACTIVE", quality_grade="C")
    assert next_option_pool_status(quality_c, seen_this_scan=True, today=TODAY) == "BLOCKED"

    blocker = _candidate(status="ACTIVE", quality_grade="B", quality_flags=["wide_spread"])
    assert next_option_pool_status(blocker, seen_this_scan=True, today=TODAY) == "BLOCKED"


def test_evaluate_watch_with_all_targets_met_becomes_ready():
    pool = _candidate(status="ACTIVE", mid=1.8, score=0.82, margin_buffer=0.18)
    watch = {
        "status": "WATCHING",
        "target_premium": 1.5,
        "target_score": 0.8,
        "target_margin_buffer": 0.15,
    }

    signal = evaluate_option_watch(pool, watch, TODAY)

    assert signal["status"] == "READY"
    assert signal["reason"] == "targets_met"
    assert signal["met_targets"] == ["premium", "score", "margin_buffer"]


def test_evaluate_watch_stays_watching_when_any_target_is_unmet():
    pool = _candidate(status="ACTIVE", mid=1.8, score=0.72, margin_buffer=0.18)
    watch = {
        "status": "WATCHING",
        "target_premium": 1.5,
        "target_score": 0.8,
        "target_margin_buffer": 0.15,
    }

    signal = evaluate_option_watch(pool, watch, TODAY)

    assert signal["status"] == "WATCHING"
    assert signal["reason"] == "targets_not_met"
    assert signal["unmet_targets"] == ["score"]


def test_evaluate_watch_without_targets_ready_for_new_or_active_pool():
    signal = evaluate_option_watch(_candidate(status="NEW"), {"status": "WATCHING"}, TODAY)

    assert signal["status"] == "READY"
    assert signal["reason"] == "no_targets_actionable"


def test_evaluate_watch_expired_for_non_terminal_watch_statuses():
    pool = _candidate(status="ACTIVE", expiration=TODAY - timedelta(days=1))

    signal = evaluate_option_watch(pool, {"status": "WATCHING"}, TODAY)

    assert signal["status"] == "EXPIRED"
    assert signal["reason"] == "contract_expired"


def test_evaluate_watch_terminal_statuses_do_not_auto_change():
    expired_pool = _candidate(status="ACTIVE", expiration=TODAY - timedelta(days=1))

    for status in ("IGNORED", "OPENED", "EXPIRED"):
        signal = evaluate_option_watch(expired_pool, {"status": status}, TODAY)
        assert signal["status"] == status
        assert signal["reason"] == "terminal_status"


def test_evaluate_watch_blocked_or_stale_pool_stays_watching_with_signal_reason():
    blocked = evaluate_option_watch(_candidate(status="BLOCKED"), {"status": "WATCHING"}, TODAY)
    stale = evaluate_option_watch(_candidate(status="STALE"), {"status": "WATCHING"}, TODAY)

    assert blocked["status"] == "WATCHING"
    assert blocked["reason"] == "pool_blocked"
    assert stale["status"] == "WATCHING"
    assert stale["reason"] == "pool_stale"
