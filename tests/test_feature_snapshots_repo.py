from __future__ import annotations

from datetime import date

from app.db.init_db import init_database
from app.db.repo import Repo


def test_init_database_migrates_existing_phase_five_database(tmp_path):
    db_path = tmp_path / "legacy.db"
    import sqlite3

    con = sqlite3.connect(db_path)
    try:
        con.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO settings(key, value) VALUES ('app', '{}');
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
              quality_grade TEXT,
              score REAL,
              dte INTEGER,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'NEW',
              UNIQUE(symbol, expiration, strike, right)
            );
            """
        )
        con.commit()
    finally:
        con.close()

    init_database(db_path)

    con = sqlite3.connect(db_path)
    try:
        candidate_cols = {row[1] for row in con.execute("PRAGMA table_info(candidates)").fetchall()}
        pool_cols = {row[1] for row in con.execute("PRAGMA table_info(option_pool)").fetchall()}
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        con.close()
    assert "state_features" in candidate_cols
    assert "state_features" in pool_cols
    assert "feature_snapshots" in tables
    assert "market_iv_snapshots" in tables


def test_market_iv_snapshot_upsert_and_history_limit(tmp_path):
    db_path = tmp_path / "features_repo.db"
    init_database(db_path)
    repo = Repo(db_path)

    repo.upsert_market_iv_snapshot(
        {
            "symbol": "aapl",
            "as_of_date": date(2026, 5, 26),
            "iv30": 0.31,
            "atm_strike": 195.0,
            "skew": 0.06,
            "vix": 18.4,
            "source": "fake",
        }
    )
    repo.upsert_market_iv_snapshot(
        {
            "symbol": "AAPL",
            "as_of_date": "2026-05-26",
            "iv30": 0.32,
            "atm_strike": 196.0,
            "skew": 0.07,
            "vix": 18.5,
            "source": "fake",
        }
    )

    rows = repo.list_market_iv_snapshots("AAPL", limit=10)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["iv30"] == 0.32
    assert rows[0]["skew"] == 0.07


def test_feature_snapshot_round_trips_json(tmp_path):
    db_path = tmp_path / "feature_snapshot.db"
    init_database(db_path)
    repo = Repo(db_path)

    feature_id = repo.insert_feature_snapshot(
        "candidate",
        42,
        {
            "rsi_14": 34.2,
            "vrp": 0.07,
            "regime": "neutral",
        },
        as_of="2026-05-26T20:00:00+00:00",
    )

    assert feature_id > 0
    latest = repo.latest_feature_snapshot("candidate", 42)
    assert latest is not None
    assert latest["features"]["vrp"] == 0.07
    assert latest["features"]["regime"] == "neutral"
