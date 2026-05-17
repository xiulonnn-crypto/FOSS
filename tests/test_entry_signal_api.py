from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core.entry_signal import build_entry_signal
from app.db.init_db import init_database
from app.db.repo import Repo
from server import create_app


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "entry_signal_api.db"
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
        "open_interest": 500,
        "quality_grade": "A",
        "quality_score": 95,
        "quality_flags": ["provider_delayed"],
        "quote_age_seconds": 900,
        "greeks_source": "provider",
        "iv_rank_source": "rv_proxy",
        "first_seen_at": datetime.now(timezone.utc).isoformat(),
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
        "status": "ACTIVE",
    }
    row.update(overrides)
    return row


def test_pool_options_returns_entry_signal_and_filters(client):
    repo = _repo(client)
    option_pool_id = repo.upsert_option_pool_rows([_pool_row()])["upserted_ids"][0]
    repo.insert_entry_signal(build_entry_signal(repo.get_option_pool(option_pool_id), today=date.today()))

    resp = client.get("/api/pool/options?status=all&entry_signal_status=OPENABLE")
    assert resp.status_code == 200
    rows = resp.get_json()["options"]
    assert len(rows) == 1
    assert rows[0]["entry_signal_status"] == "OPENABLE"
    assert rows[0]["entry_signal"]["schema"] == "entry_signal_v1"

    detail = client.get(f"/api/pool/options/{option_pool_id}/entry-signal")
    assert detail.status_code == 200
    assert detail.get_json()["entry_signal"]["status"] == "OPENABLE"


def test_watch_options_include_nested_entry_signal(client):
    repo = _repo(client)
    option_pool_id = repo.upsert_option_pool_rows([_pool_row()])["upserted_ids"][0]
    repo.insert_entry_signal(build_entry_signal(repo.get_option_pool(option_pool_id), today=date.today()))
    watch = repo.create_option_watch({"option_pool_id": option_pool_id})

    resp = client.get("/api/watch/options")

    assert resp.status_code == 200
    rows = resp.get_json()["watches"]
    assert rows[0]["id"] == watch["id"]
    assert rows[0]["option"]["entry_signal"]["status"] == "OPENABLE"
