from __future__ import annotations

import sqlite3

import pytest

from app.db.init_db import init_database
from app.db.repo import Repo


@pytest.fixture
def repo(tmp_path):
    db_path = tmp_path / "quality.db"
    init_database(db_path)
    return Repo(db_path)


def _candidate_row(scan_run_id: int, **overrides):
    row = {
        "scan_run_id": scan_run_id,
        "symbol": "AAPL",
        "expiration": "2026-06-20",
        "strike": 150.0,
        "bid": 1.5,
        "ask": 1.7,
        "mid": 1.6,
        "spot": 175.0,
        "iv": 0.28,
        "iv_rank": 60.0,
        "delta": -0.15,
        "theta": -0.03,
        "vega": 0.05,
        "gamma": 0.01,
        "dte": 35,
        "annualized_roi": 0.22,
        "pop": 0.85,
        "spread_pct": 0.125,
        "breakeven": 148.4,
        "margin_buffer": 0.143,
        "score": 0.75,
        "open_interest": 500,
    }
    row.update(overrides)
    return row


def test_insert_candidates_accepts_rows_without_quality_fields(repo):
    run_id = repo.insert_scan_run(provider="yfinance", trigger="test")

    repo.insert_candidates([_candidate_row(run_id)])

    rows = repo.list_candidates(run_id)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["quality_grade"] == "unknown"
    assert rows[0]["quality_flags"] == []


def test_insert_candidates_round_trips_quality_fields(repo):
    run_id = repo.insert_scan_run(provider="yfinance", trigger="test")

    repo.insert_candidates(
        [
            _candidate_row(
                run_id,
                quality_grade="good",
                quality_score=82,
                quality_flags=["stale_quote", "bs_greeks"],
                quote_age_seconds=93,
                greeks_source="black_scholes",
                iv_rank_source="rv_proxy",
            )
        ]
    )

    row = repo.list_candidates(run_id)[0]
    assert row["quality_grade"] == "good"
    assert row["quality_score"] == 82
    assert row["quality_flags"] == ["stale_quote", "bs_greeks"]
    assert row["quote_age_seconds"] == 93
    assert row["greeks_source"] == "black_scholes"
    assert row["iv_rank_source"] == "rv_proxy"

    with repo._connect() as con:
        stored = con.execute(
            "SELECT quality_flags FROM candidates WHERE scan_run_id=?",
            (run_id,),
        ).fetchone()["quality_flags"]
    assert stored == '["stale_quote", "bs_greeks"]'


def test_finish_scan_run_persists_diagnostics_dict(repo):
    run_id = repo.insert_scan_run(provider="yfinance", trigger="manual", symbol_count=2)
    diagnostics = {
        "symbols_failed": ["TSLA"],
        "provider_errors": {"TSLA": "timeout"},
    }

    repo.finish_scan_run(run_id, candidate_count=1, diagnostics=diagnostics)

    meta = repo.get_scan_run_meta(run_id)
    assert meta is not None
    assert meta["candidate_count"] == 1
    assert meta["diagnostics"] == diagnostics

    with repo._connect() as con:
        stored = con.execute(
            "SELECT diagnostics FROM scan_runs WHERE id=?",
            (run_id,),
        ).fetchone()["diagnostics"]
    assert "symbols_failed" in stored


def test_init_database_adds_quality_columns_to_existing_db(tmp_path):
    db_path = tmp_path / "legacy.db"
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
              trigger TEXT NOT NULL
            );
            CREATE TABLE candidates (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              scan_run_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
              symbol TEXT NOT NULL,
              expiration TEXT NOT NULL,
              strike REAL NOT NULL,
              bid REAL, ask REAL, mid REAL,
              spot REAL,
              iv REAL,
              iv_rank REAL,
              delta REAL, theta REAL, vega REAL, gamma REAL,
              dte INTEGER,
              annualized_roi REAL,
              pop REAL,
              spread_pct REAL,
              breakeven REAL,
              margin_buffer REAL,
              score REAL,
              open_interest INTEGER
            );
            CREATE TABLE positions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              symbol TEXT NOT NULL,
              expiration TEXT NOT NULL,
              strike REAL NOT NULL,
              contracts INTEGER NOT NULL,
              open_at TEXT NOT NULL,
              open_premium REAL NOT NULL,
              open_candidate_id INTEGER REFERENCES candidates(id),
              state TEXT NOT NULL,
              close_at TEXT,
              close_premium REAL,
              close_reason TEXT,
              realized_pnl REAL,
              notes TEXT
            );
            """
        )
        con.commit()
    finally:
        con.close()

    init_database(db_path)

    repo = Repo(db_path)
    run_id = repo.insert_scan_run(provider="yfinance", trigger="test")
    repo.insert_candidates([_candidate_row(run_id)])
    repo.finish_scan_run(run_id, 1, diagnostics={"migrated": True})

    row = repo.list_candidates(run_id)[0]
    assert row["quality_flags"] == []
    assert row["quality_grade"] == "unknown"
    assert repo.get_scan_run_meta(run_id)["diagnostics"] == {"migrated": True}
