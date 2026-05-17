from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.init_db import init_database
from app.db.repo import Repo
from server import create_app


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "pool_api.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
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


def test_pool_underlyings_patch_pause_archive(client):
    repo = _repo(client)
    repo.upsert_symbols(["AAPL", "TSLA"])

    resp = client.get("/api/pool/underlyings")
    assert resp.status_code == 200
    rows = resp.get_json()["underlyings"]
    assert rows[0]["pool_status"] == "ACTIVE"

    patch = client.patch(
        "/api/pool/underlyings/AAPL",
        json={"pool_status": "PAUSED", "tags": ["core"], "notes": "wait"},
    )
    assert patch.status_code == 200
    assert patch.get_json()["enabled"] == 0
    assert patch.get_json()["tags"] == ["core"]

    assert client.post("/api/pool/underlyings/AAPL/pause").status_code == 200
    archived = client.post("/api/pool/underlyings/AAPL/archive")
    assert archived.status_code == 200
    assert archived.get_json()["pool_status"] == "ARCHIVED"


def test_pool_options_filters_and_watch_flag(client):
    repo = _repo(client)
    active_id = repo.upsert_option_pool_rows([_pool_row()])["upserted_ids"][0]
    repo.upsert_option_pool_rows(
        [
            _pool_row(
                symbol="TSLA",
                strike=180.0,
                status="BLOCKED",
                quality_grade="C",
                quality_flags=["wide_spread"],
                score=0.2,
            )
        ]
    )

    default_rows = client.get("/api/pool/options").get_json()["options"]
    assert [row["id"] for row in default_rows] == [active_id]

    all_rows = client.get("/api/pool/options?status=all").get_json()["options"]
    assert {row["status"] for row in all_rows} == {"ACTIVE", "BLOCKED"}

    filtered = client.get("/api/pool/options?status=all&quality_grade=A&min_score=0.8").get_json()["options"]
    assert len(filtered) == 1
    assert filtered[0]["quality_flags"] == ["provider_delayed"]

    watch = client.post("/api/watch/options", json={"option_pool_id": active_id, "target_score": 0.8})
    assert watch.status_code == 201
    watched = client.get("/api/pool/options?status=all").get_json()["options"]
    row = next(row for row in watched if row["id"] == active_id)
    assert row["is_watched"] is True
    assert row["watch_id"] == watch.get_json()["id"]


def test_watch_options_create_duplicate_patch_and_ignore(client):
    repo = _repo(client)
    option_pool_id = repo.upsert_option_pool_rows([_pool_row()])["upserted_ids"][0]

    first = client.post(
        "/api/watch/options",
        json={"option_pool_id": option_pool_id, "watch_reason": "premium wait"},
    )
    assert first.status_code == 201
    watch_id = first.get_json()["id"]

    duplicate = client.post(
        "/api/watch/options",
        json={"option_pool_id": option_pool_id, "target_premium": 3.2},
    )
    assert duplicate.status_code == 201
    assert duplicate.get_json()["id"] == watch_id
    assert duplicate.get_json()["target_premium"] == 3.2

    patched = client.patch(
        f"/api/watch/options/{watch_id}",
        json={"notes": "still watching", "target_margin_buffer": 0.1},
    )
    assert patched.status_code == 200
    assert patched.get_json()["status"] == "WATCHING"

    ignored = client.post(
        f"/api/watch/options/{watch_id}/ignore",
        json={"ignore_reason": "manual skip"},
    )
    assert ignored.status_code == 200
    assert ignored.get_json()["status"] == "IGNORED"
    assert ignored.get_json()["ignore_reason"] == "manual skip"
