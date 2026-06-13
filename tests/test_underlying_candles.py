from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.core import underlying_candles as uc
from app.db.init_db import init_database
from app.db.repo import Repo
from server import create_app


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@pytest.fixture
def closed_position(tmp_path):
    db_path = tmp_path / "test.db"
    init_database(db_path)
    repo = Repo(db_path)

    open_at = datetime.now(timezone.utc) - timedelta(days=5)
    close_at = open_at + timedelta(days=2)
    pid = repo.insert_position({
        "symbol": "AAPL",
        "expiration": "2026-05-16",
        "strike": 150.0,
        "contracts": 1,
        "open_at": _iso(open_at),
        "open_premium": 2.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 1.0, "take_profit_50", 100.0, close_at=_iso(close_at))
    repo.save_open_snapshot(pid, {"spot": 152.0, "iv": 0.3})
    repo.save_position_close_snapshot(pid, {"mark": {"spot": 155.0}})

    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c, repo, pid, open_at, close_at


def _fake_candles(open_at, close_at):
    bars = []
    t = open_at - timedelta(days=1)
    end = close_at + timedelta(days=1)
    price = 150.0
    while t <= end:
        bars.append({
            "t": _iso(t),
            "o": price,
            "h": price + 1.0,
            "l": price - 1.0,
            "c": price + 0.5,
        })
        price += 0.5
        t += timedelta(hours=1)
    return bars


def test_build_underlying_candles_returns_markers(closed_position):
    _, repo, pid, open_at, close_at = closed_position
    with patch.object(uc, "_fetch_ohlc", return_value=_fake_candles(open_at, close_at)):
        out = uc.build_underlying_candles(repo, pid)
    assert out["available"] is True
    assert out["symbol"] == "AAPL"
    assert out["interval"] == "1h"
    assert len(out["candles"]) > 0
    assert out["entry"]["spot"] == 152.0
    assert out["exit"]["spot"] == 155.0
    assert out["entry"]["t"] is not None
    assert out["exit"]["t"] is not None


def test_endpoint_returns_candles(closed_position):
    client, repo, pid, open_at, close_at = closed_position
    with patch.object(uc, "_fetch_ohlc", return_value=_fake_candles(open_at, close_at)):
        resp = client.get(f"/api/review/positions/{pid}/underlying_candles")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["available"] is True
    assert data["entry"]["spot"] == 152.0
    assert data["exit"]["spot"] == 155.0


def test_no_candles_marks_unavailable(closed_position):
    _, repo, pid, *_ = closed_position
    with patch.object(uc, "_fetch_ohlc", return_value=[]):
        out = uc.build_underlying_candles(repo, pid)
    assert out["available"] is False
    assert out["reason"] == "no_candles"


def test_open_position_is_unavailable(tmp_path):
    db_path = tmp_path / "open.db"
    init_database(db_path)
    repo = Repo(db_path)
    pid = repo.insert_position({
        "symbol": "TSLA",
        "expiration": "2026-05-16",
        "strike": 200.0,
        "contracts": 1,
        "open_at": _iso(datetime.now(timezone.utc)),
        "open_premium": 3.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    out = uc.build_underlying_candles(repo, pid)
    assert out["available"] is False
    assert out["reason"] == "position_not_closed"


def test_missing_position_404(closed_position):
    client, *_ = closed_position
    resp = client.get("/api/review/positions/999999/underlying_candles")
    assert resp.status_code == 404
