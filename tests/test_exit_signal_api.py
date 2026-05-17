from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.init_db import init_database
from server import create_app


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "exit_api.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _create_position(client, **overrides) -> int:
    body = {
        "symbol": "AAPL",
        "expiration": (date.today() + timedelta(days=35)).isoformat(),
        "strike": 150.0,
        "contracts": 1,
        "open_premium": 2.0,
        "open_at": (datetime.now(timezone.utc) - timedelta(days=12)).isoformat(),
    }
    body.update(overrides)
    resp = client.post("/api/positions", data=json.dumps(body), content_type="application/json")
    assert resp.status_code == 201
    return int(resp.get_json()["id"])


def _mark(**overrides):
    row = {
        "spot": 175.0,
        "option_mid": 0.9,
        "mark_basis": "mid",
        "delta": -0.12,
        "margin_buffer": (175.0 - 150.0) / 175.0,
        "pnl_pct": 0.55,
        "unrealized_pnl_usd": 110.0,
    }
    row.update(overrides)
    return row


def test_positions_marks_returns_exit_signal(monkeypatch, client):
    monkeypatch.setattr("app.api.routes_positions.mark_short_put_position", lambda *a, **kw: _mark())
    _create_position(client)

    resp = client.get("/api/positions/marks")

    assert resp.status_code == 200
    row = resp.get_json()["positions"][0]
    assert row["exit_signal"]["schema"] == "exit_signal_v1"
    assert row["exit_signal"]["action"] == "TAKE_PROFIT"
    assert row["exit_signal"]["summary"]
    assert row["action_logs_count"] == 0


def test_exit_signal_endpoint_returns_latest_or_live(monkeypatch, client):
    monkeypatch.setattr("app.api.routes_positions.mark_short_put_position", lambda *a, **kw: _mark())
    position_id = _create_position(client)

    live = client.get(f"/api/positions/{position_id}/exit-signal")
    assert live.status_code == 200
    assert live.get_json()["schema"] == "exit_signal_v1"
    assert live.get_json()["action"] == "TAKE_PROFIT"

    repo = client.application.config["REPO"]
    signal_id = repo.insert_exit_signal(
        {
            "schema": "exit_signal_v1",
            "position_id": position_id,
            "action": "DEFEND",
            "severity": "danger",
            "urgency_score": 96,
            "suggested_close_reason": "loss_breach",
            "summary": "test persisted signal",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": {},
            "reasons": [],
            "legacy_signals": [],
        }
    )

    persisted = client.get(f"/api/positions/{position_id}/exit-signal")
    assert persisted.status_code == 200
    assert persisted.get_json()["id"] == signal_id
    assert persisted.get_json()["summary"] == "test persisted signal"


def test_action_log_continue_round_trip(client):
    position_id = _create_position(client)

    bad = client.post(
        f"/api/positions/{position_id}/action-log",
        json={"action_type": "CONTINUE", "reason": ""},
    )
    assert bad.status_code == 400

    created = client.post(
        f"/api/positions/{position_id}/action-log",
        json={
            "action_type": "CONTINUE",
            "reason": "等待财报后再处理",
            "notes": "仓位小，继续观察",
        },
    )
    assert created.status_code == 201

    logs = client.get(f"/api/positions/{position_id}/action-logs").get_json()
    assert len(logs) == 1
    assert logs[0]["action_type"] == "CONTINUE"
    assert logs[0]["reason"] == "等待财报后再处理"


def test_close_position_persists_exit_signal_snapshot(monkeypatch, client):
    monkeypatch.setattr("app.api.routes_positions.mark_short_put_position", lambda *a, **kw: _mark())
    position_id = _create_position(client)
    repo = client.application.config["REPO"]
    signal_id = repo.insert_exit_signal(
        {
            "schema": "exit_signal_v1",
            "position_id": position_id,
            "action": "TAKE_PROFIT",
            "severity": "warn",
            "urgency_score": 70,
            "suggested_close_reason": "take_profit_50",
            "summary": "已达到止盈阈值",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": {"pnl_pct": 0.55},
            "reasons": [],
            "legacy_signals": ["take_profit_50"],
        }
    )

    resp = client.post(
        f"/api/positions/{position_id}/close",
        json={
            "close_premium": 0.9,
            "close_reason": "take_profit_50",
            "exit_signal_id": signal_id,
            "close_notes": "按建议锁定收益",
        },
    )

    assert resp.status_code == 200
    row = client.get(f"/api/positions/{position_id}").get_json()
    assert row["close_signal_id"] == signal_id
    assert row["close_snapshot"]["exit_signal"]["schema"] == "exit_signal_v1"
    assert row["close_snapshot"]["selected_close_reason"] == "take_profit_50"
    assert row["close_snapshot"]["mark"]["radar_snapshot_id"]
    logs = client.get(f"/api/positions/{position_id}/action-logs").get_json()
    assert logs[0]["action_type"] == "CLOSE_CONFIRMED"
    assert logs[0]["exit_signal_id"] == signal_id
