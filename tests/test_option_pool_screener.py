from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

import pytest

from app.core.types import OptionContract, Quote
from app.data.provider_base import MarketDataProvider
from app.db.init_db import init_database
from app.db.repo import Repo
from app.jobs.job_screener import run_option_pool_maintenance, run_screener


class PoolFakeProvider(MarketDataProvider):
    name = "fake"
    realtime = False

    def __init__(self, *, empty_chain: bool = False):
        self.empty_chain = empty_chain

    def get_quote(self, symbol: str) -> Quote:
        return Quote(symbol=symbol, spot=175.0, asof=datetime.now(timezone.utc), iv_rank=65.0)

    def get_expirations(self, symbol: str) -> List[date]:
        return [date.today() + timedelta(days=35)]

    def get_option_chain(
        self,
        symbol,
        expiration,
        right="P",
        anchor_strike=None,
        *,
        underlying_spot=None,
    ) -> List[OptionContract]:
        if self.empty_chain:
            return []
        return [
            OptionContract(
                symbol=symbol,
                expiration=expiration,
                strike=155.0,
                right="P",
                bid=3.0,
                ask=3.2,
                last=3.1,
                iv=0.28,
                delta=-0.15,
                theta=-0.03,
                vega=0.05,
                gamma=0.01,
                open_interest=500,
                volume=100,
                quote_age_seconds=900,
            )
        ]

    def get_historical_close(self, symbol: str, day: date) -> Optional[float]:
        return 175.0

    def get_iv_history(self, symbol: str, days: int = 252) -> List[Tuple[date, float]]:
        return [(date.today() - timedelta(days=i), 0.25 + i * 0.001) for i in range(days)]

    def get_next_earnings(self, symbol: str) -> Optional[date]:
        return None


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "pool_screener.db"
    init_database(db_path)
    r = Repo(db_path)
    r.upsert_symbols(["AAPL"])
    return r


def test_screener_writes_candidates_and_option_pool(repo):
    run_screener(repo, PoolFakeProvider(), trigger="test")

    with repo._connect() as con:
        run_id = con.execute("SELECT id FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()["id"]
    assert repo.list_candidates(run_id)

    pool_rows = repo.list_option_pool(status="all")
    assert len(pool_rows) == 1
    assert pool_rows[0]["latest_candidate_id"] is not None
    assert pool_rows[0]["quality_grade"] in {"A", "B"}
    assert pool_rows[0]["entry_signal_status"] in {"OPENABLE", "WAIT"}
    assert pool_rows[0]["entry_signal"]["schema"] == "entry_signal_v1"

    meta = repo.get_scan_run_meta(run_id)
    assert meta["diagnostics"]["totals"]["option_pool_seen"] == 1
    assert meta["diagnostics"]["totals"]["option_pool_inserted"] == 1
    assert meta["diagnostics"]["totals"]["entry_signal_counts"]["generated"] == 1


def test_second_scan_updates_same_option_pool_row(repo):
    run_screener(repo, PoolFakeProvider(), trigger="test")
    first_id = repo.list_option_pool(status="all")[0]["id"]

    run_screener(repo, PoolFakeProvider(), trigger="test")
    rows = repo.list_option_pool(status="all")
    assert len(rows) == 1
    assert rows[0]["id"] == first_id
    assert rows[0]["status"] == "ACTIVE"
    assert rows[0]["missed_scan_count"] == 0


def test_empty_chain_marks_existing_pool_stale_after_two_misses(repo):
    run_screener(repo, PoolFakeProvider(), trigger="test")
    pool_id = repo.list_option_pool(status="all")[0]["id"]

    run_screener(repo, PoolFakeProvider(empty_chain=True), trigger="test")
    first_miss = repo.get_option_pool(pool_id)
    assert first_miss["missed_scan_count"] == 1
    assert first_miss["status"] != "STALE"

    run_screener(repo, PoolFakeProvider(empty_chain=True), trigger="test")
    row = repo.get_option_pool(pool_id)
    assert row["missed_scan_count"] == 2
    assert row["status"] == "STALE"


def test_watch_ready_event_deduped(repo):
    run_screener(repo, PoolFakeProvider(), trigger="test")
    pool = repo.list_option_pool(status="all")[0]
    watch = repo.create_option_watch(
        {"option_pool_id": pool["id"], "status": "WATCHING", "target_premium": 3.0}
    )

    run_screener(repo, PoolFakeProvider(), trigger="test")
    updated = repo.get_option_watch(watch["id"])
    assert updated["status"] == "READY"
    ready_events = [event for event in repo.list_events(limit=20) if event["category"] == "option_watch"]
    assert len(ready_events) == 1

    run_screener(repo, PoolFakeProvider(), trigger="test")
    ready_events = [event for event in repo.list_events(limit=20) if event["category"] == "option_watch"]
    assert len(ready_events) == 1


def test_maintenance_marks_expired_pool_and_watch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "maintenance.db"
    init_database(db_path)
    repo = Repo(db_path)
    pool_id = repo.upsert_option_pool_rows(
        [
            {
                "symbol": "AAPL",
                "expiration": (date.today() - timedelta(days=1)).isoformat(),
                "strike": 155.0,
                "right": "P",
                "first_seen_at": datetime.now(timezone.utc).isoformat(),
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
                "status": "ACTIVE",
            }
        ]
    )["upserted_ids"][0]
    watch = repo.create_option_watch({"option_pool_id": pool_id, "status": "WATCHING"})

    result = run_option_pool_maintenance(repo)
    assert result["option_pool"]["expired"] == 1
    assert repo.get_option_pool(pool_id)["status"] == "EXPIRED"
    assert repo.get_option_watch(watch["id"])["status"] == "EXPIRED"
