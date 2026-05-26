from __future__ import annotations

from datetime import datetime, timezone

from app.core.open_snapshot import build_open_snapshot_dict
from app.db.init_db import init_database
from app.db.repo import Repo


def test_open_snapshot_merges_entry_signal_from_request(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.open_snapshot.closes_through_entry", lambda *_a, **_kw: None)
    db_path = tmp_path / "entry_signal_snapshot.db"
    init_database(db_path)
    repo = Repo(db_path)
    pos = {
        "symbol": "AAPL",
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_candidate_id": None,
    }
    req = {
        "entry_signal_id": 9,
        "entry_signal_status": "WAIT",
        "entry_signal_score": 68,
        "entry_signal_summary": "建议等待：价差偏宽。",
        "entry_signal": {
            "schema": "entry_signal_v1",
            "status": "WAIT",
            "decision_score": 68,
            "summary": "建议等待：价差偏宽。",
        },
    }

    snap = build_open_snapshot_dict(repo, pos, req)

    assert snap["entry_signal_id"] == 9
    assert snap["entry_signal_status"] == "WAIT"
    assert snap["entry_signal_score"] == 68
    assert snap["entry_signal"]["schema"] == "entry_signal_v1"


def test_open_snapshot_preserves_state_features_from_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.open_snapshot.closes_through_entry", lambda *_a, **_kw: None)
    db_path = tmp_path / "state_features_snapshot.db"
    init_database(db_path)
    repo = Repo(db_path)
    run_id = repo.insert_scan_run(provider="fake", trigger="test")
    repo.insert_candidates(
        [
            {
                "scan_run_id": run_id,
                "symbol": "AAPL",
                "expiration": "2026-06-20",
                "strike": 150.0,
                "bid": 1.5,
                "ask": 1.7,
                "mid": 1.6,
                "spot": 175.0,
                "iv": 0.28,
                "iv_rank": 60.0,
                "delta": -0.15,
                "theta": -0.03,
                "vega": 0.05,
                "gamma": 0.01,
                "dte": 35,
                "annualized_roi": 0.22,
                "pop": 0.85,
                "spread_pct": 0.125,
                "breakeven": 148.4,
                "margin_buffer": 0.143,
                "score": 0.75,
                "open_interest": 500,
                "state_features": {
                    "rsi_14": 34.2,
                    "vrp": 0.07,
                    "regime": "neutral",
                },
            }
        ]
    )
    candidate = repo.list_candidates(run_id)[0]
    pos = {
        "symbol": "AAPL",
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_candidate_id": candidate["id"],
    }

    snap = build_open_snapshot_dict(repo, pos, {})

    assert snap["state_features"]["vrp"] == 0.07
    assert snap["state_features"]["regime"] == "neutral"
