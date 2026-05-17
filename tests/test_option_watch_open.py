from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.init_db import init_database
from app.db.repo import Repo
from server import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "watch_open.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    monkeypatch.setattr(
        "app.api.routes_pool.build_open_snapshot_dict",
        lambda _repo, _pos, request_data=None: {
            "option_pool_id": request_data["option_pool_id"],
            "option_watchlist_id": request_data["option_watchlist_id"],
            "quality_grade": request_data.get("quality_grade"),
            "score": request_data.get("score"),
        },
    )
    with app.test_client() as c:
        yield c


def _repo(client) -> Repo:
    return client.application.config["REPO"]


def _pool_row(**overrides):
    row = {
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
        "margin_buffer": 0.1143,
        "score": 0.82,
        "quality_grade": "A",
        "quality_score": 95,
        "quality_flags": ["provider_delayed"],
        "quote_age_seconds": 900,
        "greeks_source": "provider",
        "iv_rank_source": "rv_proxy",
        "first_seen_at": datetime.now(timezone.utc).isoformat(),
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
        "last_scan_run_id": None,
        "latest_candidate_id": None,
        "missed_scan_count": 0,
        "status": "ACTIVE",
    }
    row.update(overrides)
    return row


def test_open_from_watch_creates_position_snapshot_and_marks_opened(client):
    repo = _repo(client)
    option_pool_id = repo.upsert_option_pool_rows([_pool_row()])["upserted_ids"][0]
    watch = repo.create_option_watch({"option_pool_id": option_pool_id, "status": "READY"})

    resp = client.post(
        f"/api/watch/options/{watch['id']}/open",
        json={"open_premium": 3.05, "contracts": 1, "notes": "entered"},
    )
    assert resp.status_code == 201
    position_id = resp.get_json()["id"]

    pos = repo.get_position(position_id)
    assert pos["state"] == "OPEN"
    assert pos["symbol"] == "AAPL"
    assert pos["open_candidate_id"] is None

    snapshot = repo.get_open_snapshot(position_id)
    assert snapshot["option_pool_id"] == option_pool_id
    assert snapshot["option_watchlist_id"] == watch["id"]
    assert snapshot["quality_grade"] == "A"

    updated_watch = repo.get_option_watch(watch["id"])
    assert updated_watch["status"] == "OPENED"

    second = client.post(
        f"/api/watch/options/{watch['id']}/open",
        json={"open_premium": 3.05, "contracts": 1},
    )
    assert second.status_code == 400


def test_open_from_ignored_or_expired_watch_rejected(client):
    repo = _repo(client)
    option_pool_id = repo.upsert_option_pool_rows([_pool_row()])["upserted_ids"][0]
    watch = repo.create_option_watch({"option_pool_id": option_pool_id, "status": "IGNORED"})

    resp = client.post(
        f"/api/watch/options/{watch['id']}/open",
        json={"open_premium": 3.05, "contracts": 1},
    )
    assert resp.status_code == 400
