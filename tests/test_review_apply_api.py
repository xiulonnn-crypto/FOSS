from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.db.init_db import init_database
from app.db.repo import Repo
from server import create_app


@pytest.fixture
def apply_client(tmp_path):
    db_path = tmp_path / "apply.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    repo = Repo(db_path)
    pid = repo.insert_position({
        "symbol": "X",
        "expiration": "2026-07-18",
        "strike": 100.0,
        "contracts": 1,
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_premium": 2.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 1.0, "take_profit_50", 10.0)
    pid2 = repo.insert_position({
        "symbol": "Y",
        "expiration": "2026-07-18",
        "strike": 100.0,
        "contracts": 1,
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_premium": 2.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid2, "CLOSED_EARLY", 1.0, "take_profit_50", 80.0)
    repo.save_open_snapshot(pid, {
        "delta": -0.35,
        "margin_buffer": 0.03,
        "entry_signal_status": "WAIT",
        "quality_grade": "B",
    })
    repo.save_open_snapshot(pid2, {
        "delta": -0.12,
        "margin_buffer": 0.18,
        "entry_signal_status": "OPENABLE",
        "quality_grade": "A",
    })
    with app.test_client() as c:
        yield c


@patch("urllib.request.urlopen", side_effect=OSError("worker down"))
def test_apply_rolls_back_on_reload_failure(mock_urlopen, apply_client):
    client = apply_client
    sugg = [
        s for s in client.get("/api/review/suggestions?min_sample=1").get_json()["suggestions"]
        if s.get("changes")
    ]
    assert sugg
    resp = client.post(
        "/api/review/suggestions/apply",
        json={"suggestion_ids": [sugg[0]["id"]]},
    )
    assert resp.status_code == 409
    assert resp.get_json().get("settings_rolled_back") is True
