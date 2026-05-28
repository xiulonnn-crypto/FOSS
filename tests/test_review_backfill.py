"""Tests for review_backfill.backfill_diagnostic_fields.

Ensures the four "未知" buckets shown in #review condition-slices / order-diagnosis
drawer can be cleared by re-deriving values from existing open_snapshot fields:
``margin_buffer``, ``iv_rank``, ``quality_grade`` and ``entry_signal_status``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

from app.core.review_analytics import build_position_dimension_summary
from app.core.review_backfill import backfill_diagnostic_fields
from app.db.init_db import init_database
from app.db.repo import Repo


def _make_repo(tmp_path) -> Repo:
    db_path = tmp_path / "backfill.db"
    init_database(db_path)
    return Repo(db_path)


def _insert_manual_closed(repo: Repo, symbol: str = "MU", strike: float = 600.0) -> int:
    pid = repo.insert_position(
        {
            "symbol": symbol,
            "expiration": "2026-06-18",
            "strike": strike,
            "contracts": 1,
            "open_at": "2026-05-13T14:00:00+00:00",
            "open_premium": 8.0,
            "open_candidate_id": None,
            "state": "OPEN",
            "notes": None,
        }
    )
    repo.close_position(
        pid,
        "CLOSED_EARLY",
        4.0,
        "manual",
        400.0,
        close_at="2026-05-14T13:39:00+00:00",
    )
    return pid


def _make_settings_with_rv_history(rv_history: Optional[List[float]] = None) -> Dict[str, Any]:
    return {
        "filters": {
            "delta_min": 0.05,
            "delta_max": 0.20,
            "dte_min": 21,
            "dte_max": 60,
            "annualized_roi_min": 0.20,
            "spread_pct_max": 0.10,
            "iv_rank_min": 20,
            "margin_buffer_min": 0.10,
            "min_open_interest": 50,
        },
        "rv_by_symbol": {"MU": rv_history or [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]},
    }


def test_backfill_fills_margin_buffer_from_spot_and_strike(tmp_path):
    repo = _make_repo(tmp_path)
    pid = _insert_manual_closed(repo, strike=600.0)
    pos = repo.get_position(pid)

    snapshot = {
        "spot": 750.0,
        "delta": -0.18,
        "dte": 36,
        "iv": 0.42,
    }

    merged = backfill_diagnostic_fields(repo, pos, snapshot, _make_settings_with_rv_history())

    assert merged["margin_buffer"] == pytest.approx((750.0 - 600.0) / 750.0, rel=1e-6)


def test_backfill_skips_margin_buffer_when_already_present(tmp_path):
    repo = _make_repo(tmp_path)
    pid = _insert_manual_closed(repo, strike=600.0)
    pos = repo.get_position(pid)

    snapshot = {"spot": 750.0, "margin_buffer": 0.42, "delta": -0.1, "dte": 28, "iv": 0.4}
    merged = backfill_diagnostic_fields(repo, pos, snapshot, _make_settings_with_rv_history())
    assert merged["margin_buffer"] == pytest.approx(0.42)


def test_backfill_fills_iv_rank_via_rv_proxy(tmp_path):
    repo = _make_repo(tmp_path)
    pid = _insert_manual_closed(repo)
    pos = repo.get_position(pid)

    snapshot = {
        "spot": 750.0,
        "delta": -0.15,
        "dte": 28,
        "iv": 0.6,
    }
    settings = _make_settings_with_rv_history([0.2, 0.4, 0.6, 0.8, 1.0])

    merged = backfill_diagnostic_fields(repo, pos, snapshot, settings)

    assert merged["iv_rank"] is not None
    assert 0 <= float(merged["iv_rank"]) <= 100
    assert merged.get("iv_rank_source") == "rv_proxy"


def test_backfill_assigns_quality_grade_when_unknown(tmp_path):
    repo = _make_repo(tmp_path)
    pid = _insert_manual_closed(repo)
    pos = repo.get_position(pid)

    snapshot = {
        "spot": 750.0,
        "delta": -0.15,
        "dte": 28,
        "iv": 0.4,
    }

    merged = backfill_diagnostic_fields(repo, pos, snapshot, _make_settings_with_rv_history())

    assert merged.get("quality_grade") in {"A", "B", "C"}
    flags = merged.get("quality_flags") or []
    assert "snapshot_inferred" in flags


def test_backfill_replaces_lowercase_unknown_quality_grade(tmp_path):
    repo = _make_repo(tmp_path)
    pid = _insert_manual_closed(repo)
    pos = repo.get_position(pid)

    snapshot = {
        "spot": 750.0,
        "delta": -0.15,
        "dte": 28,
        "iv": 0.4,
        "quality_grade": "unknown",
    }
    merged = backfill_diagnostic_fields(repo, pos, snapshot, _make_settings_with_rv_history())
    assert merged.get("quality_grade") in {"A", "B", "C"}


def test_backfill_assigns_entry_signal_status(tmp_path):
    repo = _make_repo(tmp_path)
    pid = _insert_manual_closed(repo)
    pos = repo.get_position(pid)

    snapshot = {
        "spot": 750.0,
        "delta": -0.15,
        "dte": 28,
        "iv": 0.42,
    }

    merged = backfill_diagnostic_fields(repo, pos, snapshot, _make_settings_with_rv_history())

    assert merged.get("entry_signal_status") in {"OPENABLE", "WAIT", "REJECT", "EXPIRED"}
    assert isinstance(merged.get("entry_signal"), dict)
    assert merged["entry_signal"].get("status") == merged.get("entry_signal_status")


def test_backfill_keeps_existing_entry_signal_status(tmp_path):
    repo = _make_repo(tmp_path)
    pid = _insert_manual_closed(repo)
    pos = repo.get_position(pid)

    snapshot = {
        "spot": 750.0,
        "delta": -0.15,
        "dte": 28,
        "iv": 0.4,
        "entry_signal_status": "OPENABLE",
        "entry_signal": {"status": "OPENABLE", "schema": "entry_signal_v1"},
    }
    merged = backfill_diagnostic_fields(repo, pos, snapshot, _make_settings_with_rv_history())
    assert merged.get("entry_signal_status") == "OPENABLE"


def test_backfill_clears_all_unknown_buckets_for_dimension_summary(tmp_path):
    repo = _make_repo(tmp_path)
    pid = _insert_manual_closed(repo, strike=600.0)
    pos = repo.get_position(pid)

    snapshot = {
        "spot": 750.0,
        "delta": -0.16,
        "dte": 28,
        "iv": 0.42,
        "rsi_12": 55.0,
        "rsi_6": 50.0,
        "pool_source": "manual",
    }

    merged = backfill_diagnostic_fields(repo, pos, snapshot, _make_settings_with_rv_history())

    dims = build_position_dimension_summary(pos, merged)
    unknown_dims = [d for d in dims if d["bucket"] in ("UNKNOWN", "unknown")]
    assert unknown_dims == [], f"unexpected unknown buckets: {unknown_dims}"


def test_backfill_returns_a_copy_without_mutating_input(tmp_path):
    repo = _make_repo(tmp_path)
    pid = _insert_manual_closed(repo)
    pos = repo.get_position(pid)

    snapshot = {"spot": 750.0, "delta": -0.15, "dte": 28, "iv": 0.4}
    snapshot_before = dict(snapshot)
    backfill_diagnostic_fields(repo, pos, snapshot, _make_settings_with_rv_history())
    assert snapshot == snapshot_before
