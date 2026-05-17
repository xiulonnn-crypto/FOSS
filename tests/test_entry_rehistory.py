"""Tests for closed-position entry BS replay + synthetic radar."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from app.core.entry_rehistory import recalculate_closed_position_insights
from app.db.init_db import init_database
from app.db.repo import Repo


def _closed_mu_pid(repo: Repo) -> int:
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-08-01",
        "strike": 100.0,
        "contracts": 1,
        "open_at": "2026-05-10T15:30:00+00:00",
        "open_premium": 3.2,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(
        pid,
        "CLOSED_EARLY",
        1.5,
        "manual",
        170.0,
        close_at="2026-05-13T20:05:00+00:00",
    )
    return pid


@pytest.fixture
def repo_closed(tmp_path):
    p = tmp_path / "rh.db"
    init_database(p)
    repo = Repo(p)
    return repo


@patch(
    "app.core.entry_rehistory.build_open_snapshot_dict",
    lambda *_a, **_k: {},
)
@patch(
    "app.core.entry_rehistory.implied_vol_black_scholes_put",
    lambda *a, **k: 0.301,
)
@patch(
    "app.core.entry_rehistory.spot_open_estimate",
    lambda *a, **k: 118.0,
)
@patch("app.core.entry_rehistory.daily_underlying_closes")
def test_recalculate_wipes_old_radar_and_inserts_daily_replay(mock_daily, repo_closed):
    mock_daily.return_value = [
        (date(2026, 5, 10), 118.0),
        (date(2026, 5, 13), 121.25),
    ]
    pid = _closed_mu_pid(repo_closed)

    repo_closed.insert_radar_snapshot({
        "position_id": pid,
        "taken_at": datetime(2026, 5, 9, 16, 0, tzinfo=timezone.utc).isoformat(),
        "spot": 50.0,
        "current_mid": 9.99,
        "pnl_pct": 0.1,
        "delta": None,
        "margin_buffer": 0.2,
        "signals": json.dumps([]),
    })
    sn_before = len(repo_closed.list_radar_snapshots(pid, limit=500))

    out = recalculate_closed_position_insights(repo_closed, pid, risk_free_rate=0.045)
    sn_after = repo_closed.list_radar_snapshots(pid, limit=500)

    assert sn_before >= 1
    assert len(sn_after) == 2
    assert sn_after  # DESC order newest first — check synthetic tag
    assert any("synthetic_replay" in (r.get("signals") or "") for r in sn_after)

    merged = repo_closed.get_open_snapshot(pid) or {}
    assert merged.get("replay_model") == "bs_daily_close_constant_iv"
    assert merged.get("delta") is not None
    assert out["radar_rows_inserted"] == 2


@patch(
    "app.core.entry_rehistory.build_open_snapshot_dict",
    lambda *_a, **_k: {},
)
@patch(
    "app.core.entry_rehistory.implied_vol_black_scholes_put",
    lambda *a, **k: 0.301,
)
@patch(
    "app.core.entry_rehistory.spot_open_estimate",
    lambda *a, **k: 118.0,
)
@patch("app.core.entry_rehistory.daily_underlying_closes")
def test_recalculate_allows_zero_dte_same_expiration_calendar_day(mock_daily, repo_closed):
    """Opening on the expiration US/Eastern calendar day must not block entry replay."""
    mock_daily.return_value = [(date(2026, 5, 8), 118.0)]
    pid = repo_closed.insert_position({
        "symbol": "MU",
        "expiration": "2026-05-08",
        "strike": 675.0,
        "contracts": 1,
        # Same ET calendar date as expiration (afternoon NYSE session).
        "open_at": "2026-05-08T18:30:00+00:00",
        "open_premium": 3.2,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo_closed.close_position(
        pid,
        "CLOSED_EARLY",
        1.5,
        "manual",
        170.0,
        close_at="2026-05-08T21:05:00+00:00",
    )
    out = recalculate_closed_position_insights(repo_closed, pid, risk_free_rate=0.045)
    assert out["ok"] is True
    assert out["radar_rows_inserted"] >= 1


def test_recalculate_rejects_open_calendar_day_after_expiration(repo_closed):
    pid = repo_closed.insert_position({
        "symbol": "MU",
        "expiration": "2026-05-08",
        "strike": 675.0,
        "contracts": 1,
        "open_at": "2026-05-09T15:30:00+00:00",
        "open_premium": 3.2,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo_closed.close_position(
        pid,
        "CLOSED_EARLY",
        1.5,
        "manual",
        170.0,
        close_at="2026-05-09T20:05:00+00:00",
    )
    with pytest.raises(ValueError, match="open date after expiration"):
        recalculate_closed_position_insights(repo_closed, pid, risk_free_rate=0.045)


def test_recalculate_rejects_close_instant_before_open(repo_closed):
    """close_at must be >= open_at in absolute (UTC) time."""
    pid = repo_closed.insert_position({
        "symbol": "MU",
        "expiration": "2026-06-18",
        "strike": 480.0,
        "contracts": 1,
        "open_at": "2026-06-17T15:00:00+00:00",
        "open_premium": 5.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo_closed.close_position(
        pid,
        "CLOSED_EARLY",
        2.0,
        "manual",
        100.0,
        close_at="2026-06-16T20:00:00+00:00",
    )
    with pytest.raises(ValueError, match="close_at"):
        recalculate_closed_position_insights(repo_closed, pid, risk_free_rate=0.045)

