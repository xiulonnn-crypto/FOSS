"""Tests for GET /api/screener/marks — the screener-page auto-refresh endpoint.

Mirrors `/api/positions/marks`: while the user stays on #screener, the
frontend polls this endpoint once a minute to refresh the underlying spot for
every symbol referenced by the underlying pool / option pool / option watch
grid, recompute spot-derived fields (margin_buffer, breakeven) and rebuild the
entry_signal so the decision card 决策卡 reads the latest market data.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pytest

from app.core.entry_signal import build_entry_signal
from app.core.types import Quote
from app.db.init_db import init_database
from app.db.repo import Repo
from server import create_app


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "screener_marks_api.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _repo(client) -> Repo:
    return client.application.config["REPO"]


def _pool_row(symbol="AAPL", strike=155.0, spot=175.0, mid=3.1, **overrides):
    row = {
        "symbol": symbol,
        "expiration": (date.today() + timedelta(days=35)).isoformat(),
        "strike": strike,
        "right": "P",
        "bid": 3.0,
        "ask": 3.2,
        "mid": mid,
        "spot": spot,
        "iv": 0.28,
        "iv_rank": 65.0,
        "delta": -0.15,
        "dte": 35,
        "annualized_roi": 0.21,
        "spread_pct": 0.0645,
        "breakeven": strike - mid,
        "margin_buffer": (spot - strike) / spot,
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
        "state_features": {"rsi_14": 41.0, "bb_lower_distance_pct": 3.2},
    }
    row.update(overrides)
    return row


class _StubProvider:
    """Deterministic provider with `get_quote` returning preset spot per symbol."""

    name = "stub"
    realtime = False

    def __init__(self, quotes: dict[str, float], *, fail: Optional[set] = None):
        self._quotes = quotes
        self._fail = fail or set()
        self.calls: list[str] = []

    def get_quote(self, symbol: str) -> Quote:
        self.calls.append(symbol)
        if symbol in self._fail:
            raise RuntimeError(f"quote provider down for {symbol}")
        return Quote(symbol=symbol, spot=self._quotes[symbol], asof=datetime.utcnow())


@pytest.fixture
def stub_provider(monkeypatch):
    """Install a stub YFinanceProvider on the screener-marks endpoint."""

    holder: dict[str, _StubProvider] = {}

    def _install(quotes: dict[str, float], *, fail: Optional[set] = None) -> _StubProvider:
        provider = _StubProvider(quotes, fail=fail)
        # Patch the symbol imported by routes_pool — both the class and any
        # provider factory the endpoint uses.
        monkeypatch.setattr(
            "app.api.routes_pool.YFinanceProvider", lambda: provider
        )
        holder["provider"] = provider
        return provider

    return _install


def test_screener_marks_refreshes_spot_and_rebuilds_entry_signal(client, stub_provider):
    """Decision-card data source: pool option row spot/margin_buffer/entry_signal must move with the live quote."""
    repo = _repo(client)
    repo.upsert_symbols(["AAPL"])
    pool_id = repo.upsert_option_pool_rows(
        [_pool_row(symbol="AAPL", strike=155.0, spot=175.0, mid=3.1)]
    )["upserted_ids"][0]
    repo.insert_entry_signal(
        build_entry_signal(repo.get_option_pool(pool_id), today=date.today())
    )

    # New live spot — 200 (much higher than entry, margin_buffer should expand).
    stub = stub_provider({"AAPL": 200.0})

    resp = client.get("/api/screener/marks")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    payload = resp.get_json()
    assert "quoted_at" in payload
    assert stub.calls == ["AAPL"]

    options = payload["options"]
    assert len(options) == 1
    opt = options[0]
    assert opt["symbol"] == "AAPL"
    assert opt["spot"] == pytest.approx(200.0)
    # margin_buffer = (spot - strike) / spot = (200 - 155) / 200 = 0.225
    assert opt["margin_buffer"] == pytest.approx(0.225)
    # breakeven = strike - mid = 155 - 3.1 = 151.9 (mid unchanged)
    assert opt["breakeven"] == pytest.approx(151.9)

    signal = opt["entry_signal"]
    assert signal["schema"] == "entry_signal_v1"
    # entry_signal risk metrics must reflect the new spot (this is the field
    # the screener decision card 决策卡 风险卡 reads from).
    assert signal["metrics"]["risk"]["spot"] == pytest.approx(200.0)
    assert signal["metrics"]["risk"]["margin_buffer"] == pytest.approx(0.225)


def test_screener_marks_includes_underlyings_with_live_spot(client, stub_provider):
    repo = _repo(client)
    repo.upsert_symbols(["NVDA", "MSFT"])
    # NVDA is ACTIVE (default), MSFT is paused — pause-d symbols should NOT
    # trigger a provider call (saves quota during auto-refresh).
    repo.pause_pool_underlying("MSFT")
    stub = stub_provider({"NVDA": 950.5, "MSFT": 410.0})

    resp = client.get("/api/screener/marks")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert stub.calls == ["NVDA"], "paused underlyings must not be quoted"

    by_symbol = {u["symbol"]: u for u in payload["underlyings"]}
    assert by_symbol["NVDA"]["live_spot"] == pytest.approx(950.5)
    assert "live_quoted_at" in by_symbol["NVDA"]
    # Paused row passes through with no live_spot field.
    assert by_symbol["MSFT"].get("live_spot") is None


def test_screener_marks_refreshes_option_watch_grid(client, stub_provider):
    """观察池 (option watch grid) rows must also see refreshed spot + rebuilt entry_signal."""
    repo = _repo(client)
    repo.upsert_symbols(["AAPL"])
    pool_id = repo.upsert_option_pool_rows(
        [_pool_row(symbol="AAPL", strike=155.0, spot=175.0, mid=3.1)]
    )["upserted_ids"][0]
    repo.insert_entry_signal(
        build_entry_signal(repo.get_option_pool(pool_id), today=date.today())
    )
    watch = repo.create_option_watch({"option_pool_id": pool_id})

    stub_provider({"AAPL": 195.0})

    resp = client.get("/api/screener/marks")

    assert resp.status_code == 200
    watches = resp.get_json()["watches"]
    assert len(watches) == 1
    assert watches[0]["id"] == watch["id"]
    opt = watches[0]["option"]
    assert opt["spot"] == pytest.approx(195.0)
    # margin_buffer is rounded to 4 decimals (consistent with strategy.derive_csp_candidate_row).
    assert opt["margin_buffer"] == pytest.approx((195.0 - 155.0) / 195.0, abs=1e-4)
    assert opt["entry_signal"]["metrics"]["risk"]["spot"] == pytest.approx(195.0)


def test_screener_marks_records_provider_errors_per_symbol(client, stub_provider):
    """Per-symbol failures degrade gracefully — keep prior spot, expose error."""
    repo = _repo(client)
    repo.upsert_symbols(["AAPL", "TSLA"])
    repo.upsert_option_pool_rows([
        _pool_row(symbol="AAPL", strike=155.0, spot=175.0),
        _pool_row(symbol="TSLA", strike=210.0, spot=240.0),
    ])

    # TSLA quote intentionally fails; AAPL succeeds.
    stub_provider({"AAPL": 180.0, "TSLA": 0.0}, fail={"TSLA"})

    resp = client.get("/api/screener/marks")
    assert resp.status_code == 200
    payload = resp.get_json()

    by_sym = {o["symbol"]: o for o in payload["options"]}
    assert by_sym["AAPL"]["spot"] == pytest.approx(180.0)
    # TSLA falls back to the last-known spot when the provider errors.
    assert by_sym["TSLA"]["spot"] == pytest.approx(240.0)

    errors = payload["errors"]
    assert "TSLA" in errors
    assert "AAPL" not in errors


def test_screener_marks_respects_option_pool_filter(client, stub_provider):
    """The marks endpoint accepts the same filter args as /api/pool/options so
    a user-set min_dte / status filter is preserved across auto-refresh."""
    repo = _repo(client)
    repo.upsert_symbols(["AAPL"])
    near_id = repo.upsert_option_pool_rows([
        _pool_row(symbol="AAPL", strike=155.0, spot=175.0, dte=10),
        _pool_row(symbol="AAPL", strike=160.0, spot=175.0, dte=40),
    ])["upserted_ids"][0]
    assert near_id  # both rows persisted

    stub_provider({"AAPL": 178.0})

    resp = client.get("/api/screener/marks?status=NEW,ACTIVE&min_dte=30")
    assert resp.status_code == 200
    options = resp.get_json()["options"]
    assert [o["strike"] for o in options] == [160.0]
