from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import sqlite3

import pytest

from app.core.entry_signal import build_entry_signal
from app.db.init_db import init_database
from app.db.repo import Repo


@pytest.fixture
def repo(tmp_path):
    db_path = tmp_path / "entry_signal.db"
    init_database(db_path)
    return Repo(db_path)


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


def test_entry_signal_round_trips_and_updates_option_pool(repo):
    option_pool_id = repo.upsert_option_pool_rows([_pool_row()])["upserted_ids"][0]
    pool = repo.get_option_pool(option_pool_id)
    signal = build_entry_signal(pool, today=date.today())

    signal_id = repo.insert_entry_signal(signal)

    stored = repo.get_entry_signal(signal_id)
    assert stored["schema"] == "entry_signal_v1"
    assert stored["status"] == "OPENABLE"
    assert stored["metrics"]["return"]["premium"] == pytest.approx(3.1)
    assert stored["reasons"]

    latest = repo.get_latest_entry_signal(option_pool_id)
    assert latest["id"] == signal_id

    pool_after = repo.get_option_pool(option_pool_id)
    assert pool_after["latest_entry_signal_id"] == signal_id
    assert pool_after["entry_signal_status"] == "OPENABLE"
    assert pool_after["entry_signal"]["schema"] == "entry_signal_v1"


def test_list_option_pool_filters_latest_entry_signal(repo):
    first_id = repo.upsert_option_pool_rows([_pool_row()])["upserted_ids"][0]
    second_id = repo.upsert_option_pool_rows([
        _pool_row(symbol="TSLA", strike=180.0, quality_grade="C", status="BLOCKED")
    ])["upserted_ids"][0]

    repo.insert_entry_signal(build_entry_signal(repo.get_option_pool(first_id), today=date.today()))
    repo.insert_entry_signal(build_entry_signal(repo.get_option_pool(second_id), today=date.today()))

    openable = repo.list_option_pool(status="all", entry_signal_status="OPENABLE")
    rejected = repo.list_option_pool(status="all", entry_signal_status="REJECT")

    assert [row["id"] for row in openable] == [first_id]
    assert [row["id"] for row in rejected] == [second_id]


def test_init_database_migrates_phase2_option_pool_before_entry_signal_index(tmp_path):
    db_path = tmp_path / "phase2_pool.db"
    con = sqlite3.connect(db_path)
    try:
        con.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE scan_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              provider TEXT NOT NULL,
              symbol_count INTEGER,
              candidate_count INTEGER,
              snapshot_path TEXT,
              trigger TEXT NOT NULL,
              diagnostics TEXT
            );
            CREATE TABLE candidates (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              scan_run_id INTEGER NOT NULL,
              symbol TEXT NOT NULL,
              expiration TEXT NOT NULL,
              strike REAL NOT NULL,
              score REAL
            );
            CREATE TABLE option_pool (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              symbol TEXT NOT NULL,
              expiration TEXT NOT NULL,
              strike REAL NOT NULL,
              right TEXT NOT NULL DEFAULT 'P',
              dte INTEGER,
              score REAL,
              quality_grade TEXT,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              missed_scan_count INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'NEW',
              UNIQUE(symbol, expiration, strike, right)
            );
            INSERT INTO option_pool(symbol, expiration, strike, right, dte, score, quality_grade, first_seen_at, last_seen_at, status)
            VALUES('AAPL', '2026-06-20', 155, 'P', 35, 0.8, 'A', '2026-05-16T00:00:00+00:00', '2026-05-16T00:00:00+00:00', 'ACTIVE');
            """
        )
        con.commit()
    finally:
        con.close()

    init_database(db_path)

    repo = Repo(db_path)
    with repo._connect() as con:
        cols = {row["name"] for row in con.execute("PRAGMA table_info(option_pool)")}
        indexes = {
            row["name"]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        stored = con.execute("SELECT symbol FROM option_pool").fetchone()["symbol"]
    assert {
        "latest_entry_signal_id",
        "entry_signal_status",
        "entry_signal_score",
        "entry_signal_summary",
        "entry_signal_generated_at",
        "entry_signal_payload",
    } <= cols
    assert "idx_option_pool_entry_signal" in indexes
    assert stored == "AAPL"
