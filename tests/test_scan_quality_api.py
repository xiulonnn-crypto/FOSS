from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.core.types import OptionContract, Quote
from app.db.init_db import init_database
from server import create_app


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "scan_quality.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _candidate_row(scan_run_id: int):
    return {
        "scan_run_id": scan_run_id,
        "symbol": "AAPL",
        "expiration": "2026-06-20",
        "strike": 150.0,
        "bid": 1.5,
        "ask": 1.6,
        "mid": 1.55,
        "spot": 175.0,
        "iv": 0.3,
        "iv_rank": 65.0,
        "delta": -0.15,
        "theta": -0.01,
        "vega": 0.02,
        "gamma": 0.001,
        "dte": 35,
        "annualized_roi": 0.2,
        "pop": 0.85,
        "spread_pct": 0.0645,
        "breakeven": 148.45,
        "margin_buffer": 0.1429,
        "score": 0.8,
        "open_interest": 100,
        "quality_grade": "A",
        "quality_score": 100,
        "quality_flags": ["provider_delayed", "iv_rank_proxy"],
        "quote_age_seconds": 900,
        "greeks_source": "provider",
        "iv_rank_source": "rv_proxy",
    }


def test_scan_latest_returns_quality_wire_payload(client):
    repo = client.application.config["REPO"]
    rid = repo.insert_scan_run(provider="fake", trigger="manual", symbol_count=1)
    diagnostics = {
        "schema": "scan_diagnostics_v1",
        "totals": {
            "symbols": 1,
            "failed_symbols": 0,
            "contracts_seen": 1,
            "candidates": 1,
            "quality_counts": {"A": 1, "B": 0, "C": 0, "unknown": 0},
            "rejection_counts": {},
        },
        "symbols": {},
    }
    repo.insert_candidates([_candidate_row(rid)])
    repo.finish_scan_run(rid, 1, diagnostics=diagnostics)

    data = client.get("/api/scan/latest").get_json()
    row = data["candidates"][0]
    assert row["quality_grade"] == "A"
    assert row["quality_flags"] == ["provider_delayed", "iv_rank_proxy"]
    assert row["data_quality"]["grade"] == "A"
    assert row["data_quality"]["iv_rank_source"] == "rv_proxy"
    assert data["run"]["diagnostics"]["schema"] == "scan_diagnostics_v1"


def test_scan_latest_infers_quality_for_legacy_unrated_candidate_rows(client):
    repo = client.application.config["REPO"]
    rid = repo.insert_scan_run(provider="yfinance", trigger="scheduled", symbol_count=1)
    row = _candidate_row(rid)
    for key in (
        "quality_grade",
        "quality_score",
        "quality_flags",
        "quote_age_seconds",
        "greeks_source",
        "iv_rank_source",
    ):
        row.pop(key)
    repo.insert_candidates([row])
    repo.finish_scan_run(rid, 1)

    data = client.get("/api/scan/latest").get_json()
    candidate = data["candidates"][0]
    assert candidate["quality_grade"] == "B"
    assert "snapshot_inferred" in candidate["quality_flags"]
    assert candidate["data_quality"]["grade"] == "B"
    assert candidate["data_quality"]["greeks_source"] == "provider"


@patch("app.api.routes_scan.fill_greeks", lambda c, *_a, **_kw: c)
@patch("app.api.routes_scan.YFinanceProvider")
def test_scan_specific_returns_quality_wire_payload(mock_provider_cls, client):
    exp = date.today() + timedelta(days=40)
    c = OptionContract(
        symbol="AAPL",
        expiration=exp,
        strike=150.0,
        right="P",
        bid=3.0,
        ask=3.2,
        last=None,
        iv=0.3,
        delta=-0.15,
        theta=-0.01,
        vega=0.02,
        gamma=0.001,
        open_interest=120,
        volume=10,
        quote_age_seconds=900,
    )
    inst = MagicMock()
    inst.name = "yfinance"
    inst.realtime = False
    inst.get_quote.return_value = Quote(symbol="AAPL", spot=175.0, asof=datetime.now(timezone.utc), iv_rank=65.0)
    inst.get_expirations.return_value = [exp]
    inst.get_option_chain.return_value = [c]
    mock_provider_cls.return_value = inst

    resp = client.post(
        "/api/scan/specific",
        json={"symbol": "AAPL", "expiration": exp.isoformat(), "strike": 150.0},
    )
    assert resp.status_code == 200
    row = resp.get_json()["candidates"][0]
    assert row["quality_grade"] in {"A", "B"}
    assert row["data_quality"]["quote_age_seconds"] == 900
    assert "provider_delayed" in row["quality_flags"]
