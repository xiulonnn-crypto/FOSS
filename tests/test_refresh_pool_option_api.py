"""Tests for GET /api/pool/options/<id>/refresh.

The refresh endpoint pulls a *full* live snapshot for a single option pool row
(spot + chain) so the screener decision card 决策卡 reflects current market data.

When a previously-BLOCKED contract has its quotes restored, the endpoint must
re-grade its data quality and recompute ``status`` from the freshly-fetched
contract — otherwise the watch card shows live mid/spot/margin_buffer values
alongside a stale BLOCKED badge (the user-reported #screener bug).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import pytest

from app.core.types import OptionContract, Quote
from app.db.init_db import init_database
from app.db.repo import Repo
from server import create_app


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "refresh_pool_option.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _repo(client) -> Repo:
    return client.application.config["REPO"]


def _blocked_pool_row(symbol="MU", strike=750.0, expiration_days=21):
    """Mimic the persisted shape of a scanner-rejected contract: status BLOCKED,
    quality_grade C, all metrics NULL.  This is exactly the on-disk shape that
    triggered the bug (rejected_contracts in score_csp_candidates_with_diagnostics
    only carry symbol/expiration/strike/quality_* — no bid/ask/mid/dte/delta)."""
    return {
        "symbol": symbol,
        "expiration": (date.today() + timedelta(days=expiration_days)).isoformat(),
        "strike": strike,
        "right": "P",
        "bid": None,
        "ask": None,
        "mid": None,
        "spot": None,
        "iv": None,
        "iv_rank": None,
        "delta": None,
        "theta": None,
        "vega": None,
        "gamma": None,
        "dte": None,
        "annualized_roi": None,
        "spread_pct": None,
        "breakeven": None,
        "margin_buffer": None,
        "score": None,
        "open_interest": None,
        "quality_grade": "C",
        "quality_score": 0,
        "quality_flags": ["provider_delayed", "greeks_bs_fallback", "dte_out_of_range"],
        "quote_age_seconds": 900,
        "greeks_source": "missing",
        "iv_rank_source": "missing",
        "first_seen_at": datetime.now(timezone.utc).isoformat(),
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
        "status": "BLOCKED",
    }


class _StubProvider:
    """Provider stub that returns one tradeable put contract (no provider Greeks).

    Mirrors yfinance's real behavior for deep-OTM long-dated puts: bid/ask/iv/oi
    are populated but delta/theta/vega come back ``None`` — exercising the
    fill_greeks path inside refresh_pool_option.
    """

    name = "stub"
    realtime = False

    def __init__(self, *, spot: float, contracts: List[OptionContract]):
        self._spot = spot
        self._contracts = contracts
        self.quote_calls: list[str] = []
        self.chain_calls: list[tuple] = []

    def get_quote(self, symbol: str) -> Quote:
        self.quote_calls.append(symbol)
        return Quote(symbol=symbol, spot=self._spot, asof=datetime.utcnow())

    def get_option_chain(
        self,
        symbol: str,
        expiration: date,
        right: str = "P",
        anchor_strike: Optional[float] = None,
        *,
        underlying_spot: Optional[float] = None,
    ) -> List[OptionContract]:
        self.chain_calls.append((symbol, expiration, right))
        return list(self._contracts)


@pytest.fixture
def stub_provider(monkeypatch):
    holder: dict[str, _StubProvider] = {}

    def _install(*, spot: float, contracts: List[OptionContract]) -> _StubProvider:
        provider = _StubProvider(spot=spot, contracts=contracts)
        monkeypatch.setattr(
            "app.api.routes_pool.YFinanceProvider", lambda: provider
        )
        holder["provider"] = provider
        return provider

    return _install


def test_refresh_unblocks_contract_when_fresh_data_is_clean(client, stub_provider):
    """Watch-card BLOCKED bug: a stale BLOCKED row whose live chain returns
    valid quotes must re-grade out of C and stop reporting status=BLOCKED."""
    repo = _repo(client)

    # MU 2026-06-18 750P-shaped row: persisted as BLOCKED with NULL metrics
    # (the exact shape rejected_contracts produces in score_csp_candidates).
    pool_id = repo.upsert_option_pool_rows([_blocked_pool_row()])["upserted_ids"][0]
    pool = repo.get_option_pool(pool_id)
    assert pool["status"] == "BLOCKED"
    assert pool["quality_grade"] == "C"

    # Live chain returns a clean tradeable contract — bid/ask/iv/oi present
    # but no provider Greeks (yfinance does not give Greeks for deep-OTM
    # long-dated options).  fill_greeks must close the delta gap.
    expiration = date.fromisoformat(pool["expiration"])
    live_contract = OptionContract(
        symbol="MU",
        expiration=expiration,
        strike=750.0,
        right="P",
        bid=16.55,
        ask=17.15,
        last=16.85,
        iv=0.96,
        delta=None,
        theta=None,
        vega=None,
        gamma=None,
        open_interest=1884,
        volume=120,
        quote_age_seconds=900,
    )
    stub_provider(spot=947.5, contracts=[live_contract])

    resp = client.get(f"/api/pool/options/{pool_id}/refresh")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["schema"] == "option_refresh_v1"
    assert body["chain_refreshed"] is True
    opt = body["option"]

    # Fresh data is overlaid.
    assert opt["bid"] == pytest.approx(16.55)
    assert opt["ask"] == pytest.approx(17.15)
    assert opt["mid"] == pytest.approx(16.85, abs=1e-3)
    assert opt["spot"] == pytest.approx(947.5)
    assert opt["open_interest"] == 1884
    # margin_buffer = (947.5 - 750) / 947.5 ≈ 0.2085
    assert opt["margin_buffer"] == pytest.approx(0.2085, abs=1e-3)
    # DTE must be derived from the live chain fetch.
    assert opt["dte"] == 21
    # fill_greeks must close the delta gap (BS fallback, marked accordingly).
    assert opt["delta"] is not None
    assert opt["greeks_source"] == "bs_fallback"

    # Quality must be re-graded against the fresh data — it cannot remain C.
    assert opt["quality_grade"] in {"A", "B"}, (
        "fresh chain data should pull the contract out of grade C "
        f"(got {opt['quality_grade']}, flags={opt.get('quality_flags')})"
    )
    # Status must no longer be BLOCKED when quality has moved past C.
    assert opt["status"] != "BLOCKED", (
        f"refresh must recompute status; got status={opt['status']} "
        f"quality={opt['quality_grade']} flags={opt.get('quality_flags')}"
    )
    # entry_signal must not echo the stale pool_blocked / quality_c blockers.
    blocker_codes = {b.get("code") for b in (opt["entry_signal"].get("blockers") or [])}
    assert "pool_blocked" not in blocker_codes
    assert "quality_c" not in blocker_codes


def test_refresh_keeps_blocked_when_fresh_data_still_fails_quality(client, stub_provider):
    """The fix must not blindly un-BLOCK — when fresh data is still grade-C
    (e.g. no bid/ask), status must stay BLOCKED with current blocker codes."""
    repo = _repo(client)
    pool_id = repo.upsert_option_pool_rows([_blocked_pool_row()])["upserted_ids"][0]
    pool = repo.get_option_pool(pool_id)

    expiration = date.fromisoformat(pool["expiration"])
    # No bid/ask in the live chain — provider effectively still has no quote.
    bad_contract = OptionContract(
        symbol="MU",
        expiration=expiration,
        strike=750.0,
        right="P",
        bid=None,
        ask=None,
        last=None,
        iv=None,
        delta=None,
        theta=None,
        vega=None,
        gamma=None,
        open_interest=0,
        volume=0,
        quote_age_seconds=900,
    )
    stub_provider(spot=947.5, contracts=[bad_contract])

    resp = client.get(f"/api/pool/options/{pool_id}/refresh")
    assert resp.status_code == 200
    opt = resp.get_json()["option"]
    assert opt["status"] == "BLOCKED"
    assert opt["quality_grade"] == "C"
