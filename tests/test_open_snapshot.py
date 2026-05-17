from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.open_snapshot import build_open_snapshot_dict
from app.db.init_db import init_database
from app.db.repo import Repo


@pytest.fixture
def repo(tmp_path):
    p = tmp_path / "os.db"
    init_database(p)
    return Repo(p)


def _minimal_candidate_row(scan_run_id: int) -> dict:
    return {
        "scan_run_id": scan_run_id,
        "symbol": "AAPL",
        "expiration": "2026-06-20",
        "strike": 150.0,
        "bid": 1.0,
        "ask": 1.1,
        "mid": 1.05,
        "spot": 175.0,
        "iv": 0.3,
        "iv_rank": 50.0,
        "delta": -0.2,
        "theta": -0.01,
        "vega": 0.02,
        "gamma": 0.001,
        "dte": 30,
        "annualized_roi": 0.2,
        "pop": 0.7,
        "spread_pct": 0.1,
        "breakeven": 148.0,
        "margin_buffer": 0.05,
        "score": 0.9,
        "open_interest": 100,
    }


def test_build_open_snapshot_adds_technicals(monkeypatch, repo):
    monkeypatch.setattr(
        "app.core.open_snapshot.closes_through_entry",
        lambda sym, dt: [100.0 + i * 0.12 for i in range(40)],
    )
    pos = {
        "symbol": "QQQ",
        "open_at": "2026-01-15T16:00:00+00:00",
        "open_candidate_id": None,
    }
    snap = build_open_snapshot_dict(repo, pos, None)
    assert snap.get("rsi_6") is not None
    assert snap.get("bb_distance_pct") is not None


def test_build_open_snapshot_merges_candidate_without_history(monkeypatch, repo):
    monkeypatch.setattr("app.core.open_snapshot.closes_through_entry", lambda *a: None)
    rid = repo.insert_scan_run("fake", "manual", symbol_count=1)
    repo.insert_candidates([_minimal_candidate_row(rid)])
    cand_id = repo.list_candidates(rid, limit=1)[0]["id"]
    pos = {
        "symbol": "AAPL",
        "open_at": datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc).isoformat(),
        "open_candidate_id": cand_id,
    }
    snap = build_open_snapshot_dict(repo, pos, None)
    assert snap.get("delta") == pytest.approx(-0.2)
    assert snap.get("score") == pytest.approx(0.9)


def test_build_open_snapshot_merges_metrics_from_request(monkeypatch, repo):
    """Specific-search rows have Greeks in the UI body but no DB candidate id."""
    monkeypatch.setattr("app.core.open_snapshot.closes_through_entry", lambda *a: None)
    pos = {
        "symbol": "MU",
        "open_at": datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc).isoformat(),
        "open_candidate_id": None,
    }
    req_body = {
        "delta": -0.185,
        "theta": -0.022,
        "vega": 0.041,
        "iv": 0.31,
        "spot": 218.5,
        "dte": 379,
        "annualized_roi": 0.12,
        "score": 0.65,
    }
    snap = build_open_snapshot_dict(repo, pos, req_body)
    assert snap.get("delta") == pytest.approx(-0.185)
    assert snap.get("spot") == pytest.approx(218.5)
    assert snap["dte"] == 379


def test_position_open_datetime_handles_z_suffix():
    from app.core.open_snapshot import position_open_datetime

    dt = position_open_datetime({"open_at": "2026-05-01T14:30:00Z"})
    assert dt is not None
    assert dt.tzinfo is not None
