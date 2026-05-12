from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db.init_db import init_database
from server import create_app


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_get_settings(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "filters" in data
    assert "exits" in data


def test_post_settings_merge(client):
    resp = client.post(
        "/api/settings",
        data=json.dumps({"filters": {"delta_min": 0.05}}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["filters"]["delta_min"] == 0.05


def test_watchlist_crud(client):
    resp = client.post(
        "/api/watchlist",
        data=json.dumps({"symbols": "AAPL,TSLA"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    symbols = [w["symbol"] for w in resp.get_json()]
    assert "AAPL" in symbols and "TSLA" in symbols


def test_create_and_get_position(client):
    resp = client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "AAPL",
            "expiration": "2026-06-20",
            "strike": 150.0,
            "contracts": 1,
            "open_premium": 2.0,
        }),
        content_type="application/json",
    )
    assert resp.status_code == 201
    pid = resp.get_json()["id"]
    resp2 = client.get(f"/api/positions/{pid}")
    assert resp2.status_code == 200
    assert resp2.get_json()["symbol"] == "AAPL"


def test_internal_notify_rejects_external():
    """Simulate external IP — use a fresh client that patches remote_addr."""
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        init_database(db_path)
        app = create_app(db_path=db_path)
        app.config["TESTING"] = True
        with app.test_client() as c:
            # Flask test client sets REMOTE_ADDR to 127.0.0.1 by default
            # Override to simulate external call
            resp = c.post(
                "/api/internal/notify",
                data=json.dumps({"id": 1}),
                content_type="application/json",
                environ_base={"REMOTE_ADDR": "1.2.3.4"},
            )
            assert resp.status_code == 403


def test_list_positions_empty(client):
    resp = client.get("/api/positions")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_close_position(client):
    resp = client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "TSLA", "expiration": "2026-06-20",
            "strike": 200.0, "contracts": 2, "open_premium": 3.0,
        }),
        content_type="application/json",
    )
    pid = resp.get_json()["id"]
    resp2 = client.post(
        f"/api/positions/{pid}/close",
        data=json.dumps({"close_premium": 1.5, "close_reason": "take_profit_50"}),
        content_type="application/json",
    )
    assert resp2.status_code == 200
    assert resp2.get_json()["realized_pnl"] == pytest.approx((3.0 - 1.5) * 100 * 2 - 2.0)
