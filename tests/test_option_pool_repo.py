from __future__ import annotations

import sqlite3

import pytest

from app.db.init_db import init_database
from app.db.repo import Repo


@pytest.fixture
def repo(tmp_path):
    db_path = tmp_path / "pool.db"
    init_database(db_path)
    return Repo(db_path)


def _pool_row(**overrides):
    row = {
        "symbol": "AAPL",
        "expiration": "2026-06-20",
        "strike": 150.0,
        "right": "P",
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
        "quality_grade": "B",
        "quality_score": 82,
        "quality_flags": ["greeks_bs_fallback"],
        "quote_age_seconds": 300,
        "greeks_source": "bs_fallback",
        "iv_rank_source": "rv_proxy",
        "last_scan_run_id": None,
        "latest_candidate_id": None,
    }
    row.update(overrides)
    return row


def test_init_database_migrates_legacy_watchlist_and_creates_pool_tables(tmp_path):
    db_path = tmp_path / "legacy.db"
    con = sqlite3.connect(db_path)
    try:
        con.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE watchlist (
              symbol TEXT PRIMARY KEY,
              added_at TEXT NOT NULL,
              earnings_at TEXT,
              enabled INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO watchlist(symbol, added_at, enabled)
            VALUES('AAPL', '2026-05-01T00:00:00+00:00', 1);
            """
        )
        con.commit()
    finally:
        con.close()

    init_database(db_path)

    repo = Repo(db_path)
    row = repo.list_pool_underlyings()[0]
    assert row["symbol"] == "AAPL"
    assert row["pool_status"] == "ACTIVE"
    assert row["enabled"] == 1
    assert row["tags"] == []
    assert row["last_pool_summary"] == {}

    with repo._connect() as con:
        watch_cols = {r["name"] for r in con.execute("PRAGMA table_info(watchlist)")}
        tables = {
            r["name"]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"pool_status", "tags", "notes", "last_pool_summary"} <= watch_cols
    assert {"option_pool", "option_watchlist"} <= tables


def test_underlying_pool_json_fields_and_status_sync(repo):
    repo.upsert_symbols(["AAPL", "TSLA"])

    updated = repo.update_pool_underlying(
        "AAPL",
        {
            "pool_status": "PAUSED",
            "tags": ["核心科技", "ETF"],
            "notes": "wait for IV",
            "last_pool_summary": {"candidates": 3},
            "last_candidate_count": 3,
        },
    )

    assert updated["pool_status"] == "PAUSED"
    assert updated["enabled"] == 0
    assert updated["tags"] == ["核心科技", "ETF"]
    assert updated["last_pool_summary"] == {"candidates": 3}
    assert repo.list_enabled_watchlist_symbols() == ["TSLA"]

    archived = repo.archive_pool_underlying("TSLA")
    assert archived["pool_status"] == "ARCHIVED"
    assert archived["enabled"] == 0
    assert repo.list_active_underlying_symbols() == []


def test_upsert_option_pool_rows_updates_unique_contract_and_json(repo):
    first = repo.upsert_option_pool_rows([_pool_row()])
    assert first["inserted"] == 1
    assert first["updated"] == 0

    second = repo.upsert_option_pool_rows(
        [
            _pool_row(
                bid=2.0,
                ask=2.2,
                mid=2.1,
                score=0.91,
                quality_grade="A",
                quality_score=95,
                quality_flags=["provider_delayed", "iv_rank_proxy"],
            )
        ]
    )

    assert second["inserted"] == 0
    assert second["updated"] == 1
    assert second["upserted_ids"] == first["upserted_ids"]

    rows = repo.list_option_pool(status="all")
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "ACTIVE"
    assert row["bid"] == pytest.approx(2.0)
    assert row["score"] == pytest.approx(0.91)
    assert row["quality_flags"] == ["provider_delayed", "iv_rank_proxy"]

    with repo._connect() as con:
        stored = con.execute("SELECT quality_flags FROM option_pool").fetchone()["quality_flags"]
    assert stored == '["provider_delayed", "iv_rank_proxy"]'


def test_mark_pool_missed_or_expired_transitions(repo):
    active_id = repo.upsert_option_pool_rows(
        [_pool_row(expiration="2026-06-20")]
    )["upserted_ids"][0]
    expired_id = repo.upsert_option_pool_rows(
        [_pool_row(symbol="TSLA", expiration="2026-05-01", strike=120.0)]
    )["upserted_ids"][0]

    first = repo.mark_pool_missed_or_expired([], today="2026-05-16")
    second = repo.mark_pool_missed_or_expired([], today="2026-05-16")

    assert first["expired"] == 1
    assert second["stale"] == 1
    assert repo.get_option_pool(active_id)["status"] == "STALE"
    assert repo.get_option_pool(active_id)["missed_scan_count"] == 2
    assert repo.get_option_pool(expired_id)["status"] == "EXPIRED"


def test_option_watchlist_json_and_status_flow(repo):
    option_pool_id = repo.upsert_option_pool_rows([_pool_row()])["upserted_ids"][0]

    watch = repo.create_option_watch(
        {
            "option_pool_id": option_pool_id,
            "watch_reason": "wait premium",
            "target_premium": 2.0,
            "target_score": 0.8,
            "last_signal": {"status": "WATCHING"},
        }
    )

    assert watch["status"] == "WATCHING"
    assert watch["last_signal"] == {"status": "WATCHING"}
    assert watch["option"]["symbol"] == "AAPL"
    assert watch["option"]["quality_flags"] == ["greeks_bs_fallback"]

    duplicate = repo.create_option_watch(
        {
            "option_pool_id": option_pool_id,
            "target_premium": 2.2,
            "notes": "raise target",
        }
    )
    assert duplicate["id"] == watch["id"]
    assert duplicate["target_premium"] == pytest.approx(2.2)

    ready = repo.persist_option_watch_evaluation(
        watch["id"],
        status="READY",
        last_signal={"status": "READY", "reason": "target_met"},
        evaluated_at="2026-05-16T10:00:00+00:00",
    )
    assert ready["status"] == "READY"
    assert ready["last_signal"]["reason"] == "target_met"

    ignored = repo.ignore_option_watch(watch["id"], "too wide")
    assert ignored["status"] == "IGNORED"
    assert ignored["ignore_reason"] == "too wide"

    opened = repo.mark_option_watch_opened(watch["id"], {"status": "OPENED"})
    assert opened["status"] == "OPENED"
    assert opened["last_signal"] == {"status": "OPENED"}
