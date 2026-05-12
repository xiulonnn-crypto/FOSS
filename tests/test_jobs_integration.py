from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from app.core.types import OptionContract, Quote
from app.data.provider_base import MarketDataProvider
from app.db.init_db import init_database
from app.db.repo import Repo
from app.jobs.job_screener import run_screener
from app.jobs.job_radar import run_radar
from app.jobs.job_settlement import run_settlement


# ------------------------------------------------------------------
# Fake Provider
# ------------------------------------------------------------------

class FakeProvider(MarketDataProvider):
    name = "fake"
    realtime = False

    def __init__(self, spot: float = 175.0):
        self.spot = spot

    def get_quote(self, symbol: str) -> Quote:
        return Quote(
            symbol=symbol,
            spot=self.spot,
            asof=datetime.now(timezone.utc),
            iv_rank=65.0,
        )

    def get_expirations(self, symbol: str) -> List[date]:
        return [date.today() + timedelta(days=35)]

    def get_option_chain(self, symbol, expiration, right="P") -> List[OptionContract]:
        return [
            OptionContract(
                symbol=symbol,
                expiration=expiration,
                strike=155.0,
                right="P",
                bid=3.00,
                ask=3.20,
                last=3.10,
                iv=0.28,
                delta=-0.15,
                theta=-0.03,
                vega=0.05,
                gamma=0.01,
                open_interest=500,
                volume=100,
            )
        ]

    def get_historical_close(self, symbol: str, day: date) -> Optional[float]:
        return self.spot

    def get_iv_history(self, symbol: str, days: int = 252) -> List[Tuple[date, float]]:
        return [(date.today() - timedelta(days=i), 0.25 + i * 0.001) for i in range(days)]

    def get_next_earnings(self, symbol: str) -> Optional[date]:
        return None


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    db_path = tmp_path / "test.db"
    init_database(db_path)
    r = Repo(db_path)
    r.upsert_symbols(["AAPL", "TSLA"])
    return r


# ------------------------------------------------------------------
# job_screener
# ------------------------------------------------------------------

def test_screener_writes_candidates(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider()
    run_screener(repo, provider, trigger="test")
    # Find latest scan_run
    con = __import__("sqlite3").connect(str(tmp_path / "test.db"))
    count = con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    assert count > 0, "screener should write at least one candidate"
    events = repo.list_unread_events()
    screener_events = [e for e in events if e["category"] == "screener"]
    assert len(screener_events) >= 1


# ------------------------------------------------------------------
# job_radar
# ------------------------------------------------------------------

def test_radar_writes_snapshots(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Insert an OPEN position
    pos_id = repo.insert_position({
        "symbol": "AAPL",
        "expiration": str(date.today() + timedelta(days=35)),
        "strike": 155.0,
        "contracts": 1,
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_premium": 1.6,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    provider = FakeProvider(spot=175.0)
    run_radar(repo, provider)
    snaps = repo.list_radar_snapshots(pos_id)
    assert len(snaps) >= 1, "radar should write at least one snapshot"


# ------------------------------------------------------------------
# job_settlement
# ------------------------------------------------------------------

def test_settlement_otm(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Position expiring today
    pos_id = repo.insert_position({
        "symbol": "AAPL",
        "expiration": str(date.today()),
        "strike": 150.0,
        "contracts": 1,
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_premium": 2.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    provider = FakeProvider(spot=175.0)  # spot > strike → OTM
    run_settlement(repo, provider)
    pos = repo.get_position(pos_id)
    assert pos["state"] == "EXPIRED_OTM"
    assert pos["close_reason"] == "expired_otm"


def test_settlement_assigned(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pos_id = repo.insert_position({
        "symbol": "AAPL",
        "expiration": str(date.today()),
        "strike": 200.0,
        "contracts": 1,
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_premium": 2.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    provider = FakeProvider(spot=175.0)  # spot < strike → ASSIGNED
    run_settlement(repo, provider)
    pos = repo.get_position(pos_id)
    assert pos["state"] == "ASSIGNED"
    assert pos["close_reason"] == "assigned"
