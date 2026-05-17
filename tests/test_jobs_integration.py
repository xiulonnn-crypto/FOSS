from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple
from unittest.mock import MagicMock, patch

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

    def get_option_chain(
        self,
        symbol,
        expiration,
        right="P",
        anchor_strike=None,
        *,
        underlying_spot=None,
    ) -> List[OptionContract]:
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

def test_screener_preallocated_run_id(repo, tmp_path, monkeypatch):
    """Manual path: reuse existing scan_run row instead of inserting another."""
    monkeypatch.chdir(tmp_path)
    rid = repo.insert_scan_run(provider="fake", trigger="manual", symbol_count=1)
    provider = FakeProvider()
    run_screener(repo, provider, trigger="manual", run_id=rid)
    import sqlite3

    con = sqlite3.connect(str(tmp_path / "test.db"))
    n_runs = con.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0]
    last_id = con.execute("SELECT id FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    con.close()
    assert n_runs == 1
    assert last_id == rid


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


def test_screener_insert_failure_reports_zero_db_and_danger_event(
    repo, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider()
    with patch.object(Repo, "insert_candidates", side_effect=RuntimeError("boom")):
        run_screener(repo, provider, trigger="test")

    import sqlite3

    db = str(tmp_path / "test.db")
    con = sqlite3.connect(db)
    rid = con.execute("SELECT id FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    cand_count = con.execute(
        "SELECT candidate_count FROM scan_runs WHERE id=?", (rid,)
    ).fetchone()[0]
    persisted = con.execute(
        "SELECT COUNT(*) FROM candidates WHERE scan_run_id=?", (rid,)
    ).fetchone()[0]
    con.close()
    assert cand_count == 0
    assert persisted == 0

    screener_titles = [
        e["title"] for e in repo.list_events(limit=20) if e["category"] == "screener"
    ]
    assert len(screener_titles) == 1
    assert "未能写入数据库" in screener_titles[0]


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
    assert float(pos["realized_pnl"]) == pytest.approx(2.0 * 100 - 1.0)
    snaps = repo.list_radar_snapshots(pos_id)
    assert len(snaps) == 1
    assert snaps[0]["spot"] == pytest.approx(175.0)
    assert snaps[0]["current_mid"] == pytest.approx(0.0)
    # settlement must write close_snapshot so the attr-drawer shows exit data
    cs = pos.get("close_snapshot")
    assert isinstance(cs, dict), "settlement must write close_snapshot"
    assert cs["schema"] == "position_close_snapshot_v1"
    assert cs["selected_close_reason"] == "expired_otm"
    assert cs["mark"]["spot"] == pytest.approx(175.0)


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
    # intrinsic = strike - spot = 25; open + assignment option legs: 2 × fee
    assert float(pos["realized_pnl"]) == pytest.approx((2.0 - 25.0) * 100 - 2.0)
    snaps = repo.list_radar_snapshots(pos_id)
    assert len(snaps) == 1
    assert snaps[0]["spot"] == pytest.approx(175.0)
    assert snaps[0]["current_mid"] == pytest.approx(25.0)
    cs = pos.get("close_snapshot")
    assert isinstance(cs, dict), "settlement must write close_snapshot"
    assert cs["schema"] == "position_close_snapshot_v1"
    assert cs["selected_close_reason"] == "assigned"
    assert cs["mark"]["spot"] == pytest.approx(175.0)
