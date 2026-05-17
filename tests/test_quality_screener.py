from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

from app.core.types import OptionContract, Quote
from app.data.provider_base import MarketDataProvider
from app.db.init_db import init_database
from app.db.repo import Repo
from app.jobs.job_screener import run_screener


class QualityFakeProvider(MarketDataProvider):
    name = "fake"
    realtime = False

    def get_quote(self, symbol: str) -> Quote:
        return Quote(symbol=symbol, spot=175.0, asof=datetime.now(timezone.utc))

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
        return [(date.today() - timedelta(days=i), 0.2 + i * 0.001) for i in range(days)]

    def get_next_earnings(self, symbol: str) -> Optional[date]:
        return None


def test_screener_writes_quality_fields_and_scan_diagnostics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "quality_screener.db"
    init_database(db_path)
    repo = Repo(db_path)
    repo.upsert_symbols(["AAPL"])

    run_screener(repo, QualityFakeProvider(), trigger="test")

    with repo._connect() as con:
        run_id = con.execute("SELECT id FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()["id"]
    rows = repo.list_candidates(run_id)
    assert rows
    assert rows[0]["quality_grade"] in {"A", "B"}
    assert rows[0]["quality_flags"]

    meta = repo.get_scan_run_meta(run_id)
    diagnostics = meta["diagnostics"]
    assert diagnostics["schema"] == "scan_diagnostics_v1"
    assert diagnostics["totals"]["candidates"] == len(rows)
    assert diagnostics["totals"]["contracts_seen"] >= 1
    assert diagnostics["symbols"]["AAPL"]["candidates"] == len(rows)


def test_screener_symbol_error_is_diagnostic_not_fatal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "quality_screener_error.db"
    init_database(db_path)
    repo = Repo(db_path)
    repo.upsert_symbols(["BROKEN"])

    class BrokenProvider(QualityFakeProvider):
        def get_quote(self, symbol: str) -> Quote:
            raise RuntimeError("quote down")

    run_screener(repo, BrokenProvider(), trigger="test")

    with repo._connect() as con:
        run_id = con.execute("SELECT id FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()["id"]
    meta = repo.get_scan_run_meta(run_id)
    assert meta["candidate_count"] == 0
    assert meta["diagnostics"]["totals"]["failed_symbols"] == 1
    assert meta["diagnostics"]["symbols"]["BROKEN"]["status"] == "error"
