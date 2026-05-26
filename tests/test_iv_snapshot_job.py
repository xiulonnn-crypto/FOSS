from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

from app.core.types import OptionContract, Quote
from app.data.provider_base import MarketDataProvider
from app.db.init_db import init_database
from app.db.repo import Repo
from app.jobs.job_iv_snapshot import run_iv_snapshot


class IVSnapshotFakeProvider(MarketDataProvider):
    name = "fake"
    realtime = False

    def get_quote(self, symbol: str) -> Quote:
        spot = 20.0 if symbol == "^VIX" else 100.0
        return Quote(symbol=symbol, spot=spot, asof=datetime.now(timezone.utc))

    def get_expirations(self, symbol: str) -> List[date]:
        return [date.today() + timedelta(days=20), date.today() + timedelta(days=32)]

    def get_option_chain(
        self,
        symbol: str,
        expiration: date,
        right: str = "P",
        anchor_strike: Optional[float] = None,
        *,
        underlying_spot: Optional[float] = None,
    ) -> List[OptionContract]:
        del anchor_strike, underlying_spot
        if right == "P":
            return [
                _contract(symbol, expiration, "P", 100.0, 0.31, -0.5),
                _contract(symbol, expiration, "P", 90.0, 0.42, -0.25),
            ]
        return [
            _contract(symbol, expiration, "C", 100.0, 0.30, 0.5),
            _contract(symbol, expiration, "C", 110.0, 0.34, 0.25),
        ]

    def get_historical_close(self, symbol: str, day: date) -> Optional[float]:
        return 100.0

    def get_iv_history(self, symbol: str, days: int = 252) -> List[Tuple[date, float]]:
        return []


def _contract(symbol: str, expiration: date, right: str, strike: float, iv: float, delta: float) -> OptionContract:
    return OptionContract(
        symbol=symbol,
        expiration=expiration,
        strike=strike,
        right=right,
        bid=1.0,
        ask=1.1,
        last=1.05,
        iv=iv,
        delta=delta,
        theta=-0.01,
        vega=0.02,
        gamma=0.01,
        open_interest=100,
        volume=10,
    )


def test_run_iv_snapshot_records_iv30_skew_vix_and_settings_history(tmp_path):
    db_path = tmp_path / "iv_snapshot.db"
    init_database(db_path)
    repo = Repo(db_path)
    repo.upsert_symbols(["AAPL"])

    result = run_iv_snapshot(repo, IVSnapshotFakeProvider(), today=date.today())

    assert result["updated"] == 1
    rows = repo.list_market_iv_snapshots("AAPL", limit=5)
    assert len(rows) == 1
    assert rows[0]["iv30"] == 0.31
    assert rows[0]["atm_strike"] == 100.0
    assert rows[0]["skew"] == round(0.42 - 0.34, 6)
    assert rows[0]["vix"] == 20.0

    settings = repo.get_settings()
    assert settings["iv_by_symbol"]["AAPL"][-1] == 0.31
