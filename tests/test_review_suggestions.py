from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.db.init_db import init_database
from app.db.repo import Repo
from server import create_app


@pytest.fixture
def suggest_client(tmp_path):
    db_path = tmp_path / "sugg.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    repo = Repo(db_path)
    profiles = [
        {"delta": -0.35, "margin_buffer": 0.03, "entry_signal_status": "WAIT", "pnl": -20.0},
        {"delta": -0.12, "margin_buffer": 0.18, "entry_signal_status": "OPENABLE", "pnl": 80.0},
    ]
    for i in range(6):
        prof = profiles[i % len(profiles)]
        pid = repo.insert_position({
            "symbol": f"S{i}",
            "expiration": "2026-07-18",
            "strike": 100.0,
            "contracts": 1,
            "open_at": datetime.now(timezone.utc).isoformat(),
            "open_premium": 2.0,
            "open_candidate_id": None,
            "state": "OPEN",
            "notes": None,
        })
        repo.close_position(pid, "CLOSED_EARLY", 1.0, "take_profit_50", prof["pnl"])
        repo.save_open_snapshot(pid, {
            "delta": prof["delta"],
            "margin_buffer": prof["margin_buffer"],
            "entry_signal_status": prof["entry_signal_status"],
            "quality_grade": "B",
            "score": 50 + i,
        })
    with app.test_client() as c:
        yield c, repo


def test_suggestions_empty_when_min_sample_not_met(suggest_client):
    client, _repo = suggest_client
    resp = client.get("/api/review/suggestions")
    assert resp.status_code == 200
    assert resp.get_json()["suggestions"] == []


@patch("urllib.request.urlopen")
def test_apply_suggestion_updates_settings(mock_urlopen, suggest_client):
    mock_urlopen.return_value.__enter__ = lambda s: s
    mock_urlopen.return_value.__exit__ = lambda *a: None
    client, repo = suggest_client
    resp = client.get("/api/review/suggestions?min_sample=1")
    suggestions = [s for s in resp.get_json()["suggestions"] if s.get("changes")]
    assert suggestions
    sid = suggestions[0]["id"]
    apply_resp = client.post(
        "/api/review/suggestions/apply",
        json={"suggestion_ids": [sid]},
    )
    assert apply_resp.status_code == 200
    assert apply_resp.get_json().get("ok") is True
    settings = repo.get_settings()
    assert settings.get("filters") is not None or settings.get("entry_signal") is not None
