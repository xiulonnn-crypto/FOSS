from __future__ import annotations

import json
import sqlite3

import pytest

from app.db.init_db import init_database
from app.db.repo import Repo


@pytest.fixture
def repo(tmp_path):
    db_path = tmp_path / "exit_signal.db"
    init_database(db_path)
    return Repo(db_path)


def _position(**overrides):
    row = {
        "symbol": "AAPL",
        "expiration": "2026-06-20",
        "strike": 150.0,
        "contracts": 1,
        "open_at": "2026-05-01T10:00:00+00:00",
        "open_premium": 2.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    }
    row.update(overrides)
    return row


def _exit_signal(position_id: int, **overrides):
    signal = {
        "schema": "exit_signal_v1",
        "position_id": position_id,
        "action": "hold",
        "severity": "info",
        "urgency_score": 12.5,
        "suggested_close_reason": None,
        "summary": "Continue holding",
        "generated_at": "2026-05-16T10:00:00+00:00",
        "metrics": {"pnl_pct": 0.25},
        "reasons": ["no_exit_triggered"],
    }
    signal.update(overrides)
    return signal


def test_init_database_migrates_legacy_exit_signal_schema_and_defaults(tmp_path):
    db_path = tmp_path / "legacy_exit.db"
    con = sqlite3.connect(db_path)
    try:
        con.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO settings(key, value)
            VALUES('app', '{"exits":{"take_profit_pct":0.6},"schedule":{"screener_minutes":9}}');
            CREATE TABLE positions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              symbol TEXT NOT NULL,
              expiration TEXT NOT NULL,
              strike REAL NOT NULL,
              contracts INTEGER NOT NULL,
              open_at TEXT NOT NULL,
              open_premium REAL NOT NULL,
              open_candidate_id INTEGER,
              state TEXT NOT NULL,
              close_at TEXT,
              close_premium REAL,
              close_reason TEXT,
              realized_pnl REAL,
              notes TEXT
            );
            INSERT INTO positions(symbol, expiration, strike, contracts, open_at, open_premium, state)
            VALUES('AAPL', '2026-06-20', 150, 1, '2026-05-01T10:00:00+00:00', 2.0, 'OPEN');
            """
        )
        con.commit()
    finally:
        con.close()

    init_database(db_path)

    repo = Repo(db_path)
    with repo._connect() as con:
        tables = {
            row["name"]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        position_cols = {row["name"] for row in con.execute("PRAGMA table_info(positions)")}

    assert {"exit_signals", "position_action_logs"} <= tables
    assert {
        "latest_exit_signal_id",
        "exit_signal_action",
        "exit_signal_severity",
        "exit_signal_score",
        "exit_signal_summary",
        "exit_signal_generated_at",
        "exit_signal_payload",
        "close_signal_id",
        "close_snapshot",
    } <= position_cols

    settings = repo.get_settings()
    assert settings["exits"]["take_profit_pct"] == pytest.approx(0.6)
    assert settings["exits"]["fast_profit_days"] == 5
    assert settings["exits"]["fast_profit_pct"] == pytest.approx(0.5)
    assert settings["exits"]["loss_pnl_pct_warn"] == pytest.approx(-0.5)
    assert settings["exits"]["loss_pnl_pct_danger"] == pytest.approx(-1.0)
    assert settings["exits"]["expiry_hold_max_mid"] == pytest.approx(0.05)
    assert settings["exits"]["expiry_hold_min_margin_buffer"] == pytest.approx(0.05)
    assert settings["schedule"]["screener_minutes"] == 9

    position = repo.get_position(1)
    assert position["latest_exit_signal_id"] is None
    assert position["exit_signal_payload"] is None
    assert repo.get_latest_exit_signal(1) is None


def test_exit_signal_round_trips_json_and_updates_latest_position_snapshot(repo):
    position_id = repo.insert_position(_position())

    first_id = repo.insert_exit_signal(
        _exit_signal(
            position_id,
            summary="Initial hold",
            generated_at="2026-05-16T10:00:00+00:00",
            metrics={"pnl_pct": 0.2, "risk": {"dte": 35}},
        )
    )
    second_id = repo.insert_exit_signal(
        _exit_signal(
            position_id,
            action="close_now",
            severity="danger",
            urgency_score=95.0,
            suggested_close_reason="loss_breach",
            summary="Loss threshold breached",
            generated_at="2026-05-16T11:00:00+00:00",
            metrics={"pnl_pct": -1.1, "risk": {"dte": 34}},
            reasons=["loss_breach", "delta_expanded"],
        )
    )

    first = repo.get_exit_signal(first_id)
    assert first["schema"] == "exit_signal_v1"
    assert first["id"] == first_id
    assert first["exit_signal_id"] == first_id
    assert first["is_latest"] is False
    assert first["metrics"]["risk"]["dte"] == 35
    assert first["reasons"] == ["no_exit_triggered"]

    latest = repo.get_latest_exit_signal(position_id)
    assert latest["id"] == second_id
    assert latest["is_latest"] is True
    assert latest["suggested_close_reason"] == "loss_breach"
    assert latest["metrics"]["pnl_pct"] == pytest.approx(-1.1)
    assert [row["id"] for row in repo.list_exit_signals(position_id)] == [second_id, first_id]

    position = repo.get_position(position_id)
    assert position["latest_exit_signal_id"] == second_id
    assert position["exit_signal_action"] == "close_now"
    assert position["exit_signal_severity"] == "danger"
    assert position["exit_signal_score"] == pytest.approx(95.0)
    assert position["exit_signal_summary"] == "Loss threshold breached"
    assert position["exit_signal_payload"]["schema"] == "exit_signal_v1"
    assert position["exit_signal_payload"]["reasons"] == ["loss_breach", "delta_expanded"]

    with repo._connect() as con:
        rows = con.execute(
            "SELECT id, is_latest, signal_json FROM exit_signals ORDER BY id"
        ).fetchall()
        payload = con.execute(
            "SELECT exit_signal_payload FROM positions WHERE id=?",
            (position_id,),
        ).fetchone()["exit_signal_payload"]

    assert [(row["id"], row["is_latest"]) for row in rows] == [(first_id, 0), (second_id, 1)]
    assert isinstance(rows[0]["signal_json"], str)
    assert isinstance(payload, str)
    assert json.loads(payload)["suggested_close_reason"] == "loss_breach"


def test_position_action_logs_allow_optional_exit_signal(repo):
    position_id = repo.insert_position(_position())
    signal_id = repo.insert_exit_signal(_exit_signal(position_id))

    linked_id = repo.insert_position_action_log(
        position_id,
        "acknowledge",
        reason="reviewed",
        notes="Keep monitoring",
        exit_signal_id=signal_id,
        created_at="2026-05-16T11:00:00+00:00",
    )
    manual_id = repo.insert_position_action_log(
        position_id,
        "manual_note",
        reason="portfolio",
        notes="No signal attached",
        created_at="2026-05-16T12:00:00+00:00",
    )

    logs = repo.list_position_action_logs(position_id)
    assert [log["id"] for log in logs] == [manual_id, linked_id]
    assert logs[0]["exit_signal_id"] is None
    assert logs[1]["exit_signal_id"] == signal_id
    assert logs[1]["notes"] == "Keep monitoring"


def test_close_snapshot_round_trips_json_and_optional_signal(repo):
    position_id = repo.insert_position(_position())
    signal_id = repo.insert_exit_signal(
        _exit_signal(position_id, action="close_now", suggested_close_reason="take_profit_fast")
    )

    repo.save_position_close_snapshot(
        position_id,
        {"fills": [{"premium": 0.8}], "source": "manual_close"},
        close_signal_id=signal_id,
    )

    position = repo.get_position(position_id)
    assert position["close_signal_id"] == signal_id
    assert position["close_snapshot"]["fills"][0]["premium"] == pytest.approx(0.8)

    with repo._connect() as con:
        raw = con.execute(
            "SELECT close_snapshot FROM positions WHERE id=?",
            (position_id,),
        ).fetchone()["close_snapshot"]
    assert isinstance(raw, str)
    assert json.loads(raw)["source"] == "manual_close"


def test_exit_signal_event_exists_matches_action_reason_and_severity(repo):
    position_id = repo.insert_position(_position())
    repo.insert_exit_signal(
        _exit_signal(
            position_id,
            action="close_now",
            severity="warn",
            suggested_close_reason="take_profit_fast",
        )
    )
    repo.insert_exit_signal(
        _exit_signal(
            position_id,
            action="hold",
            severity="info",
            suggested_close_reason=None,
        )
    )

    assert repo.exit_signal_event_exists(
        position_id, "close_now", "take_profit_fast", "warn"
    ) is True
    assert repo.exit_signal_event_exists(position_id, "hold", None, "info") is True
    assert repo.exit_signal_event_exists(position_id, "close_now", "loss_breach", "warn") is False
    assert repo.exit_signal_event_exists(position_id, "close_now", "take_profit_fast", "danger") is False


def test_positions_without_exit_signal_are_null_safe(repo):
    position_id = repo.insert_position(_position(symbol="MSFT"))

    position = repo.get_position(position_id)
    assert position["latest_exit_signal_id"] is None
    assert position["exit_signal_action"] is None
    assert position["exit_signal_payload"] is None
    assert position["close_snapshot"] is None
    assert repo.get_latest_exit_signal(position_id) is None
    assert repo.list_exit_signals(position_id) == []
