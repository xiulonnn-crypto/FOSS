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
