from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.init_db import init_database
from app.db.repo import Repo
from app.jobs.job_radar import run_radar


@pytest.fixture
def repo(tmp_path):
    db_path = tmp_path / "radar_exit.db"
    init_database(db_path)
    return Repo(db_path)


def _position(repo: Repo, **overrides) -> int:
    row = {
        "symbol": "AAPL",
        "expiration": (date.today() + timedelta(days=35)).isoformat(),
        "strike": 150.0,
        "contracts": 1,
        "open_at": (datetime.now(timezone.utc) - timedelta(days=12)).isoformat(),
        "open_premium": 2.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    }
    row.update(overrides)
    return repo.insert_position(row)


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


def test_radar_writes_snapshot_exit_signal_and_event(monkeypatch, repo):
    position_id = _position(repo)
    monkeypatch.setattr("app.jobs.job_radar.mark_short_put_position", lambda *a, **kw: _mark())

    run_radar(repo, provider=object())

    snaps = repo.list_radar_snapshots(position_id)
    assert len(snaps) == 1
    assert json.loads(snaps[0]["signals"]) == ["take_profit_50"]

    signal = repo.get_latest_exit_signal(position_id)
    assert signal["schema"] == "exit_signal_v1"
    assert signal["action"] == "TAKE_PROFIT"
    assert signal["suggested_close_reason"] == "take_profit_50"
    assert signal["source"]["radar_snapshot_id"] == snaps[0]["id"]

    events = [e for e in repo.list_events(limit=20) if e["category"] == "radar"]
    assert len(events) == 1
    assert events[0]["payload"]["exit_signal_id"] == signal["id"]


def test_radar_dedupes_same_exit_action_event(monkeypatch, repo):
    _position(repo)
    monkeypatch.setattr("app.jobs.job_radar.mark_short_put_position", lambda *a, **kw: _mark())

    run_radar(repo, provider=object())
    run_radar(repo, provider=object())

    events = [e for e in repo.list_events(limit=20) if e["category"] == "radar"]
    assert len(events) == 1
    assert len(repo.list_exit_signals(1)) == 2


def test_radar_emits_new_event_when_action_changes_to_defend(monkeypatch, repo):
    _position(repo)
    marks = [
        _mark(),
        _mark(spot=148.0, option_mid=3.4, delta=-0.48, margin_buffer=-0.0135, pnl_pct=-0.7),
    ]
    monkeypatch.setattr("app.jobs.job_radar.mark_short_put_position", lambda *a, **kw: marks.pop(0))

    run_radar(repo, provider=object())
    run_radar(repo, provider=object())

    latest = repo.get_latest_exit_signal(1)
    assert latest["action"] == "DEFEND"
    assert latest["suggested_close_reason"] in {"loss_breach", "danger_3pct", "delta_breach"}
    events = [e for e in repo.list_events(limit=20) if e["category"] == "radar"]
    assert [e["level"] for e in events] == ["danger", "warn"]


def test_radar_quote_error_writes_unknown_signal_without_event_or_snapshot(monkeypatch, repo):
    position_id = _position(repo)
    monkeypatch.setattr(
        "app.jobs.job_radar.mark_short_put_position",
        lambda *a, **kw: {"quote_error": "provider timeout"},
    )

    run_radar(repo, provider=object())

    assert repo.list_radar_snapshots(position_id) == []
    latest = repo.get_latest_exit_signal(position_id)
    assert latest["action"] == "UNKNOWN"
    assert [e for e in repo.list_events(limit=20) if e["category"] == "radar"] == []
