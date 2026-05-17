from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.api import routes_positions
from app.db.init_db import init_database
from app.core.types import OptionContract, Quote
from server import create_app


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_get_settings(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "filters" in data
    assert "exits" in data


def test_api_unknown_post_returns_fallback_json_not_405(client):
    """POST under /api/ must not hit GET-only SPA catch-all (that yields 405)."""
    resp = client.post(
        "/api/__no_such_route__/zz",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 404
    data = resp.get_json()
    assert data.get("error") == "unknown_api_route"
    assert data.get("path") == "/api/__no_such_route__/zz"
    assert "hint" in data


def test_post_settings_merge(client):
    resp = client.post(
        "/api/settings",
        data=json.dumps({"filters": {"delta_min": 0.05}}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["filters"]["delta_min"] == 0.05


def test_watchlist_crud(client):
    resp = client.post(
        "/api/watchlist",
        data=json.dumps({"symbols": "AAPL,TSLA"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    symbols = [w["symbol"] for w in resp.get_json()]
    assert "AAPL" in symbols and "TSLA" in symbols


def test_watchlist_post_narrows_enabled_symbols(client):
    """POST must deactivate tickers removed from the comma list."""
    client.post(
        "/api/watchlist",
        data=json.dumps({"symbols": "AAPL,TSLA"}),
        content_type="application/json",
    )
    resp = client.post(
        "/api/watchlist",
        data=json.dumps({"symbols": "TSLA"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    rows = resp.get_json()
    by_sym = {r["symbol"]: r["enabled"] for r in rows}
    assert by_sym["TSLA"] == 1
    assert by_sym["AAPL"] == 0
    repo = client.application.config["REPO"]
    assert repo.list_enabled_watchlist_symbols() == ["TSLA"]


def test_watchlist_post_fullwidth_mu_normalized_to_ascii(client):
    """Fullwidth Latin (common IME) must persist as ASCII so providers see MU."""
    resp = client.post(
        "/api/watchlist",
        data=json.dumps({"symbols": "ＭＵ"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    rows = resp.get_json()
    assert not any(r["symbol"] == "ＭＵ" for r in rows)
    enabled = [r["symbol"] for r in rows if r.get("enabled") == 1]
    assert enabled == ["MU"]


def test_manual_scan_rejects_empty_watchlist(client):
    """Manual scan must not return 200 noop when there is nothing to scan."""
    resp = client.post("/api/scan/run")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data.get("ok") is False
    assert "观察名单" in (data.get("error") or "")


def test_manual_scan_accepts_nonempty_watchlist(client):
    client.post(
        "/api/watchlist",
        data=json.dumps({"symbols": "AAPL"}),
        content_type="application/json",
    )
    resp = client.post("/api/scan/run")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("ok") is True
    assert isinstance(data.get("run_id"), int) and data["run_id"] > 0


def test_manual_scan_after_watchlist_save_reenables_disabled(client):
    repo = client.application.config["REPO"]
    now = "2026-05-01T10:00:00+00:00"
    with repo._connect() as con:
        con.execute(
            "INSERT INTO watchlist(symbol, added_at, enabled) VALUES(?,?,0)",
            ("NVDA", now),
        )
    assert client.post("/api/scan/run").status_code == 400
    client.post(
        "/api/watchlist",
        data=json.dumps({"symbols": "NVDA"}),
        content_type="application/json",
    )
    assert client.post("/api/scan/run").status_code == 200


def test_scan_specific_requires_symbol(client):
    r = client.post("/api/scan/specific", json={})
    assert r.status_code == 400


def test_scan_specific_invalid_expiration(client):
    r = client.post(
        "/api/scan/specific",
        json={"symbol": "AAPL", "expiration": "not-a-date", "strike": 150.0},
    )
    assert r.status_code == 400


@patch("app.api.routes_scan.fill_greeks", lambda c, *_a, **_kw: c)
@patch("app.api.routes_scan.YFinanceProvider")
def test_scan_specific_returns_one_row(mock_provider_cls, client):
    exp = date.today() + timedelta(days=40)
    oat = datetime(2026, 5, 1, tzinfo=timezone.utc)
    c = OptionContract(
        symbol="AAPL",
        expiration=exp,
        strike=150.0,
        right="P",
        bid=1.5,
        ask=1.6,
        last=None,
        iv=0.3,
        delta=-0.15,
        theta=-0.01,
        vega=0.02,
        gamma=0.001,
        open_interest=120,
        volume=10,
    )
    inst = MagicMock()
    inst.get_quote.return_value = Quote(symbol="AAPL", spot=175.0, asof=oat)
    inst.get_expirations.return_value = [exp]
    inst.get_option_chain.return_value = [c]
    mock_provider_cls.return_value = inst
    resp = client.post(
        "/api/scan/specific",
        json={"symbol": "aapl", "expiration": exp.isoformat(), "strike": 150.0},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("schema") == "scan_latest_v2"
    assert isinstance(data.get("candidates"), list) and len(data["candidates"]) == 1
    row = data["candidates"][0]
    assert row["symbol"] == "AAPL"
    assert abs(row["strike"] - 150.0) < 1e-9
    run = data.get("run") or {}
    assert run.get("trigger") == "specific"
    mock_provider_cls.return_value.get_option_chain.assert_called_once()


def test_scan_latest_empty_database(client):
    r = client.get("/api/scan/latest")
    assert r.status_code == 200
    assert r.get_json() == {
        "schema": "scan_latest_v2",
        "candidates": [],
        "run": None,
    }


def test_scan_latest_includes_finished_run_meta(client):
    repo = client.application.config["REPO"]
    rid = repo.insert_scan_run(provider="fake", trigger="manual", symbol_count=1)
    repo.finish_scan_run(rid, 0, None)
    r = client.get("/api/scan/latest")
    data = r.get_json()
    assert data.get("schema") == "scan_latest_v2"
    assert data["run"]["id"] == rid
    assert data["candidates"] == []
    assert data["run"]["finished_at"] is not None


def test_scan_run_detail_by_id(client):
    repo = client.application.config["REPO"]
    rid = repo.insert_scan_run(provider="fake", trigger="manual", symbol_count=2)
    r = client.get(f"/api/scan/run/{rid}")
    assert r.status_code == 200
    data = r.get_json()
    assert data["run"]["id"] == rid
    assert data["run"]["finished_at"] is None
    assert data["candidates"] == []


def test_scan_run_detail_404(client):
    assert client.get("/api/scan/run/99999").status_code == 404


def test_scan_latest_prefers_finished_run_over_newer_unfinished(client):
    """Older finished run with rows must beat a newer unfinished run on /scan/latest."""
    repo = client.application.config["REPO"]

    def _minimal_candidate(scan_run_id: int):
        return {
            "scan_run_id": scan_run_id,
            "symbol": "AAPL",
            "expiration": "2026-06-20",
            "strike": 150.0,
            "bid": 1.0,
            "ask": 1.1,
            "mid": 1.05,
            "spot": 175.0,
            "iv": 0.3,
            "iv_rank": 50.0,
            "delta": -0.2,
            "theta": -0.01,
            "vega": 0.02,
            "gamma": 0.001,
            "dte": 30,
            "annualized_roi": 0.2,
            "pop": 0.7,
            "spread_pct": 0.1,
            "breakeven": 148.0,
            "margin_buffer": 0.05,
            "score": 0.9,
            "open_interest": 100,
        }

    r_finished = repo.insert_scan_run(provider="fake", trigger="manual", symbol_count=1)
    repo.insert_candidates([_minimal_candidate(r_finished)])
    repo.finish_scan_run(r_finished, 1, None)

    repo.insert_scan_run(provider="fake", trigger="scheduled", symbol_count=1)

    resp = client.get("/api/scan/latest")
    data = resp.get_json()
    assert data.get("schema") == "scan_latest_v2"
    assert data["run"]["id"] == r_finished
    assert data["run"]["finished_at"] is not None
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["symbol"] == "AAPL"


def test_create_and_get_position(client):
    resp = client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "AAPL",
            "expiration": "2026-06-20",
            "strike": 150.0,
            "contracts": 1,
            "open_premium": 2.0,
        }),
        content_type="application/json",
    )
    assert resp.status_code == 201
    pid = resp.get_json()["id"]
    resp2 = client.get(f"/api/positions/{pid}")
    assert resp2.status_code == 200
    assert resp2.get_json()["symbol"] == "AAPL"


def test_create_position_inline_metrics_persist_open_snapshot(monkeypatch, client):
    """POST body may carry scan-row Greeks when open_candidate_id is absent (specific search)."""
    monkeypatch.setattr(
        "app.core.open_snapshot.closes_through_entry",
        lambda sym, dt: None,
    )
    resp = client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "MU",
            "expiration": "2026-05-15",
            "strike": 680.0,
            "contracts": 1,
            "open_premium": 3.25,
            "delta": -0.12,
            "theta": -0.048,
            "vega": 0.035,
            "iv": 0.28,
            "spot": 105.5,
            "dte": 30,
            "annualized_roi": 0.15,
            "score": 0.72,
        }),
        content_type="application/json",
    )
    assert resp.status_code == 201
    pid = resp.get_json()["id"]
    snap = client.get(f"/api/review/positions/{pid}/snapshot").get_json()
    row = snap.get("open_snapshot") or {}
    assert row.get("delta") == pytest.approx(-0.12)
    assert row.get("spot") == pytest.approx(105.5)
    body = client.get(f"/api/review/positions/{pid}/attribution").get_json()
    assert body.get("spot_open") == pytest.approx(105.5)


def test_attribution_mae_after_inline_entry_and_close(monkeypatch, client):
    """端到端：无 open_candidate_id 时由 POST 体形同行希腊；平仓写入雷达后即可分解归因。"""
    monkeypatch.setattr(
        "app.core.open_snapshot.closes_through_entry",
        lambda sym, dt: None,
    )

    def _fake_mark(pos, provider, risk_free_rate=0.045, *, prefetched_quote=None):
        return {
            "spot": 108.0,
            "option_mid": 2.5,
            "delta": -0.11,
            "margin_buffer": 0.09,
            "pnl_pct": 0.12,
            "quote_error": False,
        }

    monkeypatch.setattr(
        "app.api.routes_positions.mark_short_put_position",
        _fake_mark,
    )
    resp = client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "MU",
            "expiration": "2026-05-15",
            "strike": 680.0,
            "contracts": 1,
            "open_premium": 10.0,
            "delta": -0.10,
            "theta": -0.02,
            "spot": 100.0,
            "dte": 30,
        }),
        content_type="application/json",
    )
    assert resp.status_code == 201
    pid = resp.get_json()["id"]
    c = client.post(
        f"/api/positions/{pid}/close",
        data=json.dumps({"close_premium": 5.0, "close_reason": "manual"}),
        content_type="application/json",
    )
    assert c.status_code == 200
    body = client.get(f"/api/review/positions/{pid}/attribution").get_json()
    assert body.get("delta_contribution") is not None
    assert body.get("data_available") is True
    # Relative MAE/MFE vs first bar require >=2 radar snapshots; single close mark has (None, None).
    assert body.get("radar_points") >= 1
    if (body.get("radar_points") or 0) < 2:
        assert body.get("mae") is None and body.get("mfe") is None


def test_internal_notify_rejects_external():
    """Simulate external IP — use a fresh client that patches remote_addr."""
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        init_database(db_path)
        app = create_app(db_path=db_path)
        app.config["TESTING"] = True
        with app.test_client() as c:
            # Flask test client sets REMOTE_ADDR to 127.0.0.1 by default
            # Override to simulate external call
            resp = c.post(
                "/api/internal/notify",
                data=json.dumps({"id": 1}),
                content_type="application/json",
                environ_base={"REMOTE_ADDR": "1.2.3.4"},
            )
            assert resp.status_code == 403


def test_list_positions_empty(client):
    resp = client.get("/api/positions")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_close_position(client):
    resp = client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "TSLA", "expiration": "2026-06-20",
            "strike": 200.0, "contracts": 2, "open_premium": 3.0,
        }),
        content_type="application/json",
    )
    pid = resp.get_json()["id"]
    resp2 = client.post(
        f"/api/positions/{pid}/close",
        data=json.dumps({"close_premium": 1.5, "close_reason": "take_profit_50"}),
        content_type="application/json",
    )
    assert resp2.status_code == 200
    assert resp2.get_json()["realized_pnl"] == pytest.approx((3.0 - 1.5) * 100 * 2 - 4.0)


def test_close_position_appends_radar_snapshot(monkeypatch, client):
    """平仓时应写入一条 radar_snapshots，复盘抽屉才有收盘价等增量。"""

    def _fake_mark(pos, provider, risk_free_rate=0.045, *, prefetched_quote=None):
        return {
            "spot": 222.0,
            "option_mid": 0.25,
            "delta": -0.05,
            "margin_buffer": 0.1,
            "pnl_pct": 0.5,
        }

    monkeypatch.setattr("app.api.routes_positions.mark_short_put_position", _fake_mark)

    resp = client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "TSLA",
            "expiration": "2026-06-20",
            "strike": 200.0,
            "contracts": 1,
            "open_premium": 0.5,
        }),
        content_type="application/json",
    )
    pid = resp.get_json()["id"]
    assert client.get(f"/api/positions/{pid}/radar").get_json() == []

    client.post(
        f"/api/positions/{pid}/close",
        data=json.dumps({"close_premium": 0.25, "close_reason": "test"}),
        content_type="application/json",
    )
    snaps = client.get(f"/api/positions/{pid}/radar").get_json()
    assert len(snaps) >= 1
    latest = snaps[0]
    assert latest["spot"] == pytest.approx(222.0)
    assert latest["current_mid"] == pytest.approx(0.25)


def test_close_position_radar_taken_at_matches_close_at(monkeypatch, client):
    def _fake_mark(pos, provider, risk_free_rate=0.045, *, prefetched_quote=None):
        return {
            "spot": 100.0,
            "option_mid": 0.1,
            "delta": None,
            "margin_buffer": 0.05,
            "pnl_pct": 0.2,
        }

    monkeypatch.setattr("app.api.routes_positions.mark_short_put_position", _fake_mark)

    resp = client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "TSLA",
            "expiration": "2026-06-20",
            "strike": 200.0,
            "contracts": 1,
            "open_premium": 1.0,
        }),
        content_type="application/json",
    )
    pid = resp.get_json()["id"]
    ca = "2026-03-15T17:30:00+00:00"
    client.post(
        f"/api/positions/{pid}/close",
        data=json.dumps({
            "close_premium": 0.5,
            "close_reason": "manual",
            "close_at": ca,
        }),
        content_type="application/json",
    )
    row = client.get(f"/api/positions/{pid}").get_json()
    latest = client.get(f"/api/positions/{pid}/radar").get_json()[0]
    assert "2026-03-15T17:30:00" in row["close_at"]
    assert "2026-03-15T17:30:00" in latest["taken_at"]


def test_close_position_expiry_auto(client):
    """到期自动平仓：价外到期，0 买回，手续费按单边，状态与系统结算一致。"""
    resp = client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "TSLA",
            "expiration": "2026-06-20",
            "strike": 200.0,
            "contracts": 2,
            "open_premium": 3.0,
        }),
        content_type="application/json",
    )
    pid = resp.get_json()["id"]
    resp2 = client.post(
        f"/api/positions/{pid}/close",
        data=json.dumps({"expiry_auto": True}),
        content_type="application/json",
    )
    assert resp2.status_code == 200
    assert resp2.get_json()["realized_pnl"] == pytest.approx(3.0 * 100 * 2 - 2.0)
    row = client.get(f"/api/positions/{pid}").get_json()
    assert row["state"] == "EXPIRED_OTM"
    assert row["close_reason"] == "expired_otm"
    assert row["close_premium"] == 0.0
    assert "2026-06-20T20:00:00" in row["close_at"] or "2026-06-20T21:00:00" in row["close_at"]


def test_positions_marks_empty(client):
    r = client.get("/api/positions/marks")
    assert r.status_code == 200
    data = r.get_json()
    assert data["positions"] == []
    assert "quoted_at" in data and data["quoted_at"]


def test_positions_marks_shape_with_mock(monkeypatch, client):
    def _fake_mark(pos, provider, risk_free_rate=0.045, *, prefetched_quote=None):
        return {
            "spot": 100.0,
            "spot_asof": "2026-05-01T12:00:00+00:00",
            "option_mid": 0.5,
            "delta": -0.1,
            "margin_buffer": 0.2,
            "pnl_pct": 0.75,
            "unrealized_pnl_usd": 150.0,
        }

    monkeypatch.setattr("app.api.routes_positions.mark_short_put_position", _fake_mark)

    client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "QQQ",
            "expiration": "2026-07-18",
            "strike": 400.0,
            "contracts": 1,
            "open_premium": 2.0,
        }),
        content_type="application/json",
    )
    r = client.get("/api/positions/marks")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["positions"]) == 1
    row = data["positions"][0]
    assert row["symbol"] == "QQQ"
    assert row["mark"]["spot"] == 100.0
    assert row["mark"]["unrealized_pnl_usd"] == 150.0


def test_positions_marks_get_quote_once_per_symbol(monkeypatch, client):
    """Same underlying twice: route must not call provider.get_quote per row (avoids yfinance stampedes)."""
    quote_calls: list[str] = []

    class StubProv:
        def get_quote(self, symbol: str):
            quote_calls.append(symbol)
            return Quote(symbol=symbol, spot=180.0, asof=datetime.now(timezone.utc))

        def get_option_chain(self, *args, **kwargs):
            return []

    monkeypatch.setattr(routes_positions, "YFinanceProvider", StubProv)

    body = lambda sym, strike: json.dumps({
        "symbol": sym,
        "expiration": "2026-06-20",
        "strike": strike,
        "contracts": 1,
        "open_premium": 3.0,
    })
    assert client.post("/api/positions", data=body("TSLA", 100.0), content_type="application/json").status_code == 201
    assert client.post("/api/positions", data=body("TSLA", 105.0), content_type="application/json").status_code == 201
    assert client.post("/api/positions", data=body("AAPL", 150.0), content_type="application/json").status_code == 201

    r = client.get("/api/positions/marks")
    assert r.status_code == 200
    assert len(r.get_json()["positions"]) == 3
    assert quote_calls.count("TSLA") == 1
    assert quote_calls.count("AAPL") == 1


def test_positions_marks_fast_uses_cached_signal_without_provider(monkeypatch, client):
    def _boom_provider():
        raise AssertionError("fast marks should not instantiate provider")

    monkeypatch.setattr(routes_positions, "YFinanceProvider", _boom_provider)

    resp = client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "AAPL",
            "expiration": "2026-06-20",
            "strike": 150.0,
            "contracts": 1,
            "open_premium": 2.0,
        }),
        content_type="application/json",
    )
    pid = resp.get_json()["id"]
    repo = client.application.config["REPO"]
    signal_id = repo.insert_exit_signal({
        "schema": "exit_signal_v1",
        "position_id": pid,
        "action": "TAKE_PROFIT",
        "severity": "warn",
        "urgency_score": 72,
        "suggested_close_reason": "take_profit_50",
        "summary": "cached signal",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {"mark_basis": "mid"},
        "metrics": {
            "spot": 175.0,
            "current_mid": 0.9,
            "pnl_pct": 0.55,
            "delta": -0.12,
            "margin_buffer": 0.14,
            "unrealized_pnl_usd": 110.0,
        },
        "reasons": [],
        "legacy_signals": ["take_profit_50"],
    })
    repo.insert_position_action_log(pid, "CONTINUE", reason="等待")

    r = client.get("/api/positions/marks?fast=1")
    assert r.status_code == 200
    data = r.get_json()
    assert data["mode"] == "fast"
    row = data["positions"][0]
    assert row["latest_exit_signal_id"] == signal_id
    assert row["exit_signal"]["action"] == "TAKE_PROFIT"
    assert row["mark"]["cached"] is True
    assert row["mark"]["option_mid"] == pytest.approx(0.9)
    assert row["action_logs_count"] == 1


def test_positions_marks_reuses_option_chain_per_symbol_expiration(monkeypatch, client):
    quote_calls: list[str] = []
    chain_calls: list[tuple[str, str, object]] = []
    exp = (date.today() + timedelta(days=45)).isoformat()

    class StubProv:
        def get_quote(self, symbol: str):
            quote_calls.append(symbol)
            return Quote(symbol=symbol, spot=180.0, asof=datetime.now(timezone.utc))

        def get_option_chain(
            self,
            symbol,
            expiration,
            right="P",
            anchor_strike=None,
            *,
            underlying_spot=None,
        ):
            chain_calls.append((symbol, expiration.isoformat(), anchor_strike))
            strikes = [100.0, 105.0] if symbol == "TSLA" else [150.0]
            return [
                OptionContract(
                    symbol=symbol,
                    expiration=expiration,
                    strike=strike,
                    right="P",
                    bid=0.9,
                    ask=1.1,
                    last=None,
                    iv=0.25,
                    delta=-0.12,
                    theta=None,
                    vega=None,
                    gamma=None,
                    open_interest=100,
                    volume=10,
                )
                for strike in strikes
            ]

    inst = StubProv()
    monkeypatch.setattr(routes_positions, "YFinanceProvider", lambda: inst)

    def body(sym, strike):
        return json.dumps({
            "symbol": sym,
            "expiration": exp,
            "strike": strike,
            "contracts": 1,
            "open_premium": 3.0,
        })

    assert client.post("/api/positions", data=body("TSLA", 100.0), content_type="application/json").status_code == 201
    assert client.post("/api/positions", data=body("TSLA", 105.0), content_type="application/json").status_code == 201
    assert client.post("/api/positions", data=body("AAPL", 150.0), content_type="application/json").status_code == 201

    r = client.get("/api/positions/marks")
    assert r.status_code == 200
    assert len(r.get_json()["positions"]) == 3
    assert quote_calls.count("TSLA") == 1
    assert quote_calls.count("AAPL") == 1
    assert sorted(chain_calls) == [("AAPL", exp, None), ("TSLA", exp, None)]


def test_create_position_with_open_at(client):
    resp = client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "AAPL",
            "expiration": "2026-06-20",
            "strike": 150.0,
            "contracts": 1,
            "open_premium": 2.0,
            "open_at": "2026-01-10T15:30:00Z",
        }),
        content_type="application/json",
    )
    assert resp.status_code == 201
    body = resp.get_json()
    assert "2026-01-10T15:30:00+00:00" in body["open_at"] or body["open_at"].startswith(
        "2026-01-10T15:30:00"
    )


def test_close_position_with_close_at(client):
    resp = client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "TSLA",
            "expiration": "2026-06-20",
            "strike": 200.0,
            "contracts": 2,
            "open_premium": 3.0,
        }),
        content_type="application/json",
    )
    pid = resp.get_json()["id"]
    resp2 = client.post(
        f"/api/positions/{pid}/close",
        data=json.dumps({
            "close_premium": 1.5,
            "close_reason": "take_profit_50",
            "close_at": "2026-02-01T20:00:00+00:00",
        }),
        content_type="application/json",
    )
    assert resp2.status_code == 200
    resp3 = client.get(f"/api/positions/{pid}")
    assert resp3.status_code == 200
    cat = resp3.get_json()["close_at"]
    assert "2026-02-01T20:00:00" in cat


def test_patch_open_position(client):
    client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "XOM",
            "expiration": "2026-08-01",
            "strike": 100.0,
            "contracts": 1,
            "open_premium": 1.25,
        }),
        content_type="application/json",
    )
    listed = client.get("/api/positions").get_json()
    pid = next(p["id"] for p in listed if p["symbol"] == "XOM")
    r2 = client.patch(
        f"/api/positions/{pid}",
        data=json.dumps({
            "notes": "edited",
            "open_at": "2026-03-01T10:00:00Z",
        }),
        content_type="application/json",
    )
    assert r2.status_code == 200
    assert r2.get_json()["notes"] == "edited"


def test_router_positions_patch_always_targets_blueprint(client):
    """`/api/*` must not satisfy the SPA catch-all; PATCH must resolve to blueprint."""
    adapter = client.application.url_map.bind("127.0.0.1", "/")
    endpoint, values = adapter.match("/api/positions/2", method="PATCH")
    assert endpoint == "positions.patch_position"
    assert values.get("position_id") == 2


def test_patch_position_accepts_trailing_slash_not_static_405(client):
    """Trailing slash must hit the API route, not SPA catch-all (PATCH → 405)."""
    client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "SLB",
            "expiration": "2026-08-01",
            "strike": 50.0,
            "contracts": 1,
            "open_premium": 1.0,
        }),
        content_type="application/json",
    )
    listed = client.get("/api/positions").get_json()
    pid = next(p["id"] for p in listed if p["symbol"] == "SLB")
    r_slash = client.patch(
        f"/api/positions/{pid}/",
        data=json.dumps({"notes": "via-slash"}),
        content_type="application/json",
    )
    assert r_slash.status_code == 200, r_slash.get_data(as_text=True)[:200]
    assert r_slash.get_json()["notes"] == "via-slash"

    r_put = client.put(
        f"/api/positions/{pid}/",
        data=json.dumps({"notes": "via-put"}),
        content_type="application/json",
    )
    assert r_put.status_code == 200
    assert r_put.get_json()["notes"] == "via-put"


def test_patch_open_rejects_close_fields(client):
    listed = client.get("/api/positions").get_json()
    open_ids = [p["id"] for p in listed if p["state"] == "OPEN"]
    if not open_ids:
        client.post(
            "/api/positions",
            data=json.dumps({
                "symbol": "CVX",
                "expiration": "2026-09-01",
                "strike": 120.0,
                "contracts": 1,
                "open_premium": 1.5,
            }),
            content_type="application/json",
        )
        listed = client.get("/api/positions").get_json()
        open_ids = [p["id"] for p in listed if p["state"] == "OPEN"]
    pid = open_ids[0]
    r = client.patch(
        f"/api/positions/{pid}",
        data=json.dumps({"close_premium": 1.0}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_patch_closed_position_times(client):
    resp = client.post(
        "/api/positions",
        data=json.dumps({
            "symbol": "WMT",
            "expiration": "2026-07-01",
            "strike": 90.0,
            "contracts": 1,
            "open_premium": 2.0,
        }),
        content_type="application/json",
    )
    pid = resp.get_json()["id"]
    client.post(
        f"/api/positions/{pid}/close",
        data=json.dumps({"close_premium": 0.5, "close_reason": "manual"}),
        content_type="application/json",
    )
    r2 = client.patch(
        f"/api/positions/{pid}",
        data=json.dumps({
            "close_at": "2026-04-10T18:00:00Z",
            "open_at": "2026-01-05T14:00:00Z",
        }),
        content_type="application/json",
    )
    assert r2.status_code == 200
    row = r2.get_json()
    assert "2026-04-10T18:00:00" in row["close_at"]
    assert "2026-01-05T14:00:00" in row["open_at"]


def test_index_tailwind_play_cdn_not_deferred(client):
    """Tailwind Play CDN scans the DOM after load; defer delays it until after parse and
    can yield a prolonged or persistent unstyled view while only inline CSS (e.g. .nav-link) applies.
    """
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for line in body.splitlines():
        if "cdn.tailwindcss.com" in line:
            assert "defer" not in line
            assert "async" not in line
            return
    raise AssertionError("missing Tailwind Play CDN script in index.html")
