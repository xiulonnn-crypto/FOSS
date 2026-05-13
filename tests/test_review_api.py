from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db.init_db import init_database
from server import create_app


@pytest.fixture
def client_with_data(tmp_path):
    db_path = tmp_path / "test.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True

    from app.db.repo import Repo
    repo = Repo(db_path)

    # Insert 3 closed positions
    def _pos(symbol, strike, open_p, close_p, state, reason, pnl):
        return {
            "symbol": symbol,
            "expiration": "2026-05-16",
            "strike": strike,
            "contracts": 1,
            "open_at": datetime.now(timezone.utc).isoformat(),
            "open_premium": open_p,
            "open_candidate_id": None,
            "state": "OPEN",
            "notes": None,
        }

    p1_id = repo.insert_position(_pos("AAPL", 150.0, 2.0, 0.0, "EXPIRED_OTM", "expired_otm", 199.0))
    repo.close_position(p1_id, "EXPIRED_OTM", 0.0, "expired_otm", 199.0)

    p2_id = repo.insert_position(_pos("TSLA", 200.0, 3.0, 1.5, "CLOSED_EARLY", "take_profit_50", 148.0))
    repo.close_position(p2_id, "CLOSED_EARLY", 1.5, "take_profit_50", 148.0)

    p3_id = repo.insert_position(_pos("MSFT", 350.0, 4.0, 6.0, "ASSIGNED", "assigned", -202.0))
    repo.close_position(p3_id, "ASSIGNED", 6.0, "assigned", -202.0)

    with app.test_client() as c:
        yield c


def test_review_summary_returns_correct_trade_count(client_with_data):
    resp = client_with_data.get("/api/review/summary")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["trade_count"] == 3


def test_review_summary_includes_closed_positions(client_with_data):
    resp = client_with_data.get("/api/review/summary")
    data = resp.get_json()
    cp = data.get("closed_positions")
    assert isinstance(cp, list)
    assert len(cp) == 3
    symbols = {r["symbol"] for r in cp}
    assert symbols == {"AAPL", "TSLA", "MSFT"}


def test_review_summary_total_realized_pnl(client_with_data):
    resp = client_with_data.get("/api/review/summary")
    data = resp.get_json()
    # 199 + 148 - 202
    assert data["total_realized_pnl"] == pytest.approx(145.0)


def test_review_summary_win_rate(client_with_data):
    resp = client_with_data.get("/api/review/summary")
    data = resp.get_json()
    # 2 wins (AAPL +199, TSLA +148), 1 loss (MSFT -202)
    assert abs(data["win_rate"] - 2/3) < 0.01


def test_review_summary_by_close_reason(client_with_data):
    resp = client_with_data.get("/api/review/summary")
    data = resp.get_json()
    reasons = {r["close_reason"] for r in data["by_close_reason"]}
    assert "expired_otm" in reasons
    assert "take_profit_50" in reasons
    assert "assigned" in reasons


def test_review_csv_download(client_with_data):
    resp = client_with_data.get("/api/review/positions.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.content_type
    text = resp.data.decode("utf-8")
    assert "symbol" in text
    assert "AAPL" in text
    assert "TSLA" in text


def test_review_closed_positions_list(client_with_data):
    resp = client_with_data.get("/api/review/closed_positions")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "positions" in data
    rows = data["positions"]
    assert len(rows) == 3
    symbols = {r["symbol"] for r in rows}
    assert symbols == {"AAPL", "TSLA", "MSFT"}
    for r in rows:
        assert r["state"] in ("EXPIRED_OTM", "CLOSED_EARLY", "ASSIGNED")
        assert r.get("close_at")


def test_review_summary_empty(tmp_path):
    db_path = tmp_path / "empty.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        resp = c.get("/api/review/summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["trade_count"] == 0
        assert data["win_rate"] is None
        assert data.get("total_realized_pnl") is None
        assert data.get("closed_positions") == []

        r2 = c.get("/api/review/closed_positions")
        assert r2.status_code == 200
        assert r2.get_json()["positions"] == []
