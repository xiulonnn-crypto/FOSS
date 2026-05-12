from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.db.repo import Repo


@pytest.fixture
def db_path(tmp_path):
    from app.db.init_db import init_database
    p = tmp_path / "test.db"
    init_database(p)
    return p


@pytest.fixture
def repo(db_path):
    return Repo(db_path)


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------

def test_get_settings_returns_defaults(repo):
    s = repo.get_settings()
    assert "filters" in s
    assert s["filters"]["delta_min"] == 0.1


def test_save_and_reload_settings(repo):
    s = repo.get_settings()
    s["filters"]["delta_min"] = 0.05
    repo.save_settings(s)
    reloaded = repo.get_settings()
    assert reloaded["filters"]["delta_min"] == 0.05


def test_merge_settings_partial(repo):
    result = repo.merge_settings({"filters": {"delta_min": 0.08}})
    assert result["filters"]["delta_min"] == 0.08
    # Other keys should survive
    assert "dte_min" in result["filters"]


# ------------------------------------------------------------------
# Scan runs & candidates
# ------------------------------------------------------------------

def test_insert_scan_run_and_finish(repo):
    run_id = repo.insert_scan_run(provider="yfinance", trigger="manual", symbol_count=3)
    assert isinstance(run_id, int) and run_id > 0
    repo.finish_scan_run(run_id, candidate_count=5)


def test_insert_candidates_and_list(repo):
    run_id = repo.insert_scan_run(provider="yfinance", trigger="test")
    rows = [
        {
            "scan_run_id": run_id,
            "symbol": "AAPL",
            "expiration": "2026-06-20",
            "strike": 150.0,
            "bid": 1.5, "ask": 1.7, "mid": 1.6,
            "spot": 175.0, "iv": 0.28, "iv_rank": 60.0,
            "delta": -0.15, "theta": -0.03, "vega": 0.05, "gamma": 0.01,
            "dte": 35, "annualized_roi": 0.22, "pop": 0.85,
            "spread_pct": 0.125, "breakeven": 148.4,
            "margin_buffer": 0.143, "score": 0.75, "open_interest": 500,
        }
    ]
    repo.insert_candidates(rows)
    result = repo.list_candidates(run_id)
    assert len(result) == 1
    assert result[0]["symbol"] == "AAPL"


# ------------------------------------------------------------------
# Positions
# ------------------------------------------------------------------

def test_insert_and_list_position(repo):
    pos = {
        "symbol": "AAPL",
        "expiration": "2026-06-20",
        "strike": 150.0,
        "contracts": 1,
        "open_at": "2026-05-01T10:00:00+00:00",
        "open_premium": 1.6,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    }
    pid = repo.insert_position(pos)
    assert pid > 0
    positions = repo.list_positions(state="OPEN")
    assert any(p["id"] == pid for p in positions)


def test_close_position(repo):
    pos = {
        "symbol": "TSLA", "expiration": "2026-06-20",
        "strike": 200.0, "contracts": 2,
        "open_at": "2026-05-01T10:00:00+00:00",
        "open_premium": 3.0, "open_candidate_id": None,
        "state": "OPEN", "notes": None,
    }
    pid = repo.insert_position(pos)
    repo.close_position(pid, "CLOSED_EARLY", 1.5, "take_profit_50", 150.0 * 2)
    p = repo.get_position(pid)
    assert p["state"] == "CLOSED_EARLY"
    assert p["close_reason"] == "take_profit_50"


# ------------------------------------------------------------------
# Events
# ------------------------------------------------------------------

def test_insert_event_and_list_unread(repo):
    eid = repo.insert_event("warn", "radar", "Take profit 50%", {"position_id": 1, "signal_type": "take_profit_50"})
    assert eid > 0
    unread = repo.list_unread_events()
    assert any(e["id"] == eid for e in unread)


def test_ack_event(repo):
    eid = repo.insert_event("info", "screener", "Scan complete")
    repo.ack_event(eid)
    unread = repo.list_unread_events()
    assert not any(e["id"] == eid for e in unread)


def test_event_signal_exists_dedup(repo):
    run_id = repo.insert_scan_run(provider="yfinance", trigger="test")
    pos = {
        "symbol": "AAPL", "expiration": "2026-06-20",
        "strike": 150.0, "contracts": 1,
        "open_at": "2026-05-01T10:00:00+00:00",
        "open_premium": 1.5, "open_candidate_id": None,
        "state": "OPEN", "notes": None,
    }
    pid = repo.insert_position(pos)
    repo.insert_event("warn", "radar", "Take profit", {"position_id": pid, "signal_type": "take_profit_50"})
    assert repo.event_signal_exists(pid, "take_profit_50") is True
    assert repo.event_signal_exists(pid, "time_14d") is False
