from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.init_db import init_database
from app.db.repo import Repo
from server import create_app


@pytest.fixture
def review_filter_client(tmp_path):
    db_path = tmp_path / "filter.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    repo = Repo(db_path)

    def _insert(symbol: str, close_reason: str, pnl: float):
        pid = repo.insert_position({
            "symbol": symbol,
            "expiration": "2026-06-20",
            "strike": 100.0,
            "contracts": 1,
            "open_at": datetime(2026, 1, 10, tzinfo=timezone.utc).isoformat(),
            "open_premium": 2.0,
            "open_candidate_id": None,
            "state": "OPEN",
            "notes": None,
        })
        repo.close_position(
            pid,
            "CLOSED_EARLY",
            1.0,
            close_reason,
            pnl,
            close_at=datetime(2026, 2, 15, tzinfo=timezone.utc).isoformat(),
        )
        return pid

    p_aapl = _insert("AAPL", "take_profit_50", 100.0)
    p_spy = _insert("SPY", "manual", 50.0)
    repo.save_open_snapshot(p_aapl, {"pool_source": "main", "score": 80, "margin_buffer": 0.12})
    repo.save_open_snapshot(p_spy, {
        "option_watchlist_id": 1,
        "pool_source": "watch",
        "score": 70,
        "margin_buffer": 0.10,
    })

    with app.test_client() as client:
        yield client


def test_review_summary_symbol_filter(review_filter_client):
    resp = review_filter_client.get("/api/review/summary?symbols=AAPL&min_sample=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["trade_count"] == 1
    assert data["closed_positions"][0]["symbol"] == "AAPL"


def test_review_summary_pool_filter(review_filter_client):
    resp = review_filter_client.get("/api/review/summary?pool=watch&min_sample=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["trade_count"] == 1
    assert data["closed_positions"][0]["symbol"] == "SPY"


def test_review_summary_since_until(review_filter_client):
    resp = review_filter_client.get("/api/review/summary?since=2026-02-01&until=2026-02-28&min_sample=1")
    assert resp.status_code == 200
    assert resp.get_json()["trade_count"] == 2
