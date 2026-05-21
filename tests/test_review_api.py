from __future__ import annotations

import statistics
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from app.db.init_db import init_database
from app.db.repo import Repo
from server import create_app


@pytest.fixture
def client_with_data(tmp_path):
    db_path = tmp_path / "test.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True

    from app.db.repo import Repo
    repo = Repo(db_path)

    # Insert 3 closed positions
    def _pos(symbol, strike, open_p, close_p, state, reason, pnl):
        return {
            "symbol": symbol,
            "expiration": "2026-05-16",
            "strike": strike,
            "contracts": 1,
            "open_at": datetime.now(timezone.utc).isoformat(),
            "open_premium": open_p,
            "open_candidate_id": None,
            "state": "OPEN",
            "notes": None,
        }

    p1_id = repo.insert_position(_pos("AAPL", 150.0, 2.0, 0.0, "EXPIRED_OTM", "expired_otm", 199.0))
    repo.close_position(p1_id, "EXPIRED_OTM", 0.0, "expired_otm", 199.0)

    p2_id = repo.insert_position(_pos("TSLA", 200.0, 3.0, 1.5, "CLOSED_EARLY", "take_profit_50", 148.0))
    repo.close_position(p2_id, "CLOSED_EARLY", 1.5, "take_profit_50", 148.0)

    p3_id = repo.insert_position(_pos("MSFT", 350.0, 4.0, 6.0, "ASSIGNED", "assigned", -202.0))
    repo.close_position(p3_id, "ASSIGNED", 6.0, "assigned", -202.0)

    with app.test_client() as c:
        yield c


def test_review_summary_returns_correct_trade_count(client_with_data):
    resp = client_with_data.get("/api/review/summary")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["trade_count"] == 3


def test_review_summary_includes_closed_positions(client_with_data):
    resp = client_with_data.get("/api/review/summary")
    data = resp.get_json()
    cp = data.get("closed_positions")
    assert isinstance(cp, list)
    assert len(cp) == 3
    symbols = {r["symbol"] for r in cp}
    assert symbols == {"AAPL", "TSLA", "MSFT"}


def test_review_summary_total_realized_pnl(client_with_data):
    resp = client_with_data.get("/api/review/summary")
    data = resp.get_json()
    # 199 + 148 - 202
    assert data["total_realized_pnl"] == pytest.approx(145.0)


def test_review_summary_win_rate(client_with_data):
    resp = client_with_data.get("/api/review/summary")
    data = resp.get_json()
    # 2 wins (AAPL +199, TSLA +148), 1 loss (MSFT -202)
    assert abs(data["win_rate"] - 2/3) < 0.01


def test_review_summary_by_close_reason(client_with_data):
    resp = client_with_data.get("/api/review/summary")
    data = resp.get_json()
    reasons = {r["close_reason"] for r in data["by_close_reason"]}
    assert "expired_otm" in reasons
    assert "take_profit_50" in reasons
    assert "assigned" in reasons


def test_review_csv_download(client_with_data):
    resp = client_with_data.get("/api/review/positions.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.content_type
    text = resp.data.decode("utf-8")
    assert "symbol" in text
    assert "AAPL" in text
    assert "TSLA" in text


def test_review_closed_positions_list(client_with_data):
    resp = client_with_data.get("/api/review/closed_positions")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "positions" in data
    rows = data["positions"]
    assert len(rows) == 3
    symbols = {r["symbol"] for r in rows}
    assert symbols == {"AAPL", "TSLA", "MSFT"}
    for r in rows:
        assert r["state"] in ("EXPIRED_OTM", "CLOSED_EARLY", "ASSIGNED")
        assert r.get("close_at")


def test_review_soft_delete_closed_hides_from_lists_and_summary(client_with_data):
    repo = client_with_data.application.config["REPO"]
    rows_before = client_with_data.get("/api/review/closed_positions").get_json()["positions"]
    pid_aapl = next(r["id"] for r in rows_before if r["symbol"] == "AAPL")

    r_del = client_with_data.post(f"/api/review/positions/{pid_aapl}/delete")
    assert r_del.status_code == 200
    assert r_del.get_json().get("ok") is True

    assert repo.get_position(pid_aapl)["state"] == "DELETED"

    rows_after = client_with_data.get("/api/review/closed_positions").get_json()["positions"]
    assert len(rows_after) == 2
    assert all(r["symbol"] != "AAPL" for r in rows_after)

    summary = client_with_data.get("/api/review/summary").get_json()
    assert summary["trade_count"] == 2
    assert len(summary["closed_positions"]) == 2

    csv_text = client_with_data.get("/api/review/positions.csv").data.decode("utf-8")
    assert "AAPL" not in csv_text

    r_idle = client_with_data.post(f"/api/review/positions/{pid_aapl}/delete")
    assert r_idle.status_code == 200
    assert r_idle.get_json().get("already_deleted") is True


def test_review_soft_delete_open_rejected(client_with_data):
    repo = client_with_data.application.config["REPO"]
    open_pid = repo.insert_position({
        "symbol": "OPENZ",
        "expiration": "2026-07-18",
        "strike": 100.0,
        "contracts": 1,
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_premium": 2.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    r = client_with_data.post(f"/api/review/positions/{open_pid}/delete")
    assert r.status_code == 400


def test_review_summary_empty(tmp_path):
    db_path = tmp_path / "empty.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        resp = c.get("/api/review/summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["trade_count"] == 0
        assert data["win_rate"] is None
        assert data.get("total_realized_pnl") is None
        assert data.get("closed_positions") == []

        r2 = c.get("/api/review/closed_positions")
        assert r2.status_code == 200
        assert r2.get_json()["positions"] == []


def test_review_summary_includes_condition_slices_and_setting_suggestions(client_with_data):
    repo = client_with_data.application.config["REPO"]
    rows = client_with_data.get("/api/review/closed_positions").get_json()["positions"]
    ids = {r["symbol"]: r["id"] for r in rows}

    repo.save_open_snapshot(ids["AAPL"], {
        "quality_grade": "A",
        "entry_signal_status": "OPENABLE",
        "delta": -0.12,
        "dte": 35,
        "iv_rank": 55,
        "margin_buffer": 0.18,
    })
    repo.save_open_snapshot(ids["TSLA"], {
        "quality_grade": "B",
        "entry_signal_status": "WAIT",
        "delta": -0.28,
        "dte": 55,
        "iv_rank": 78,
        "margin_buffer": 0.05,
    })
    repo.save_open_snapshot(ids["MSFT"], {
        "quality_grade": "B",
        "entry_signal_status": "WAIT",
        "delta": -0.25,
        "dte": 18,
        "iv_rank": 22,
        "margin_buffer": 0.04,
    })

    resp = client_with_data.get("/api/review/summary?min_sample=1")

    assert resp.status_code == 200
    data = resp.get_json()
    slices = {row["factor"]: row for row in data["condition_slices"]}
    assert "factor_slices" not in data
    assert {
        "quality_grade",
        "entry_signal_status",
        "delta_abs",
        "dte",
        "iv_rank",
        "margin_buffer",
        "rsi",
        "close_reason",
        "pool_source",
    } <= set(slices)
    assert data.get("slices") == {row["factor"]: row["buckets"] for row in data["condition_slices"]}
    assert "performance_review" in data
    assert "score_pnl_correlation" in data
    assert data.get("avg_realized_roe") is not None

    quality = {b["bucket"]: b for b in slices["quality_grade"]["buckets"]}
    assert quality["A"]["count"] == 1
    assert quality["B"]["count"] == 2

    delta = {b["bucket"]: b for b in slices["delta_abs"]["buckets"]}
    assert delta["target_0_10_0_20"]["count"] == 1
    assert delta["gt_0_20"]["count"] == 2
    assert delta["gt_0_20"]["win_rate"] == pytest.approx(0.5)

    suggestion_keys = {s.get("setting_key") for s in data["setting_suggestions"]}
    assert "filters.margin_buffer_min" in suggestion_keys
    assert suggestion_keys & {"filters.delta_max", "entry_signal.openable_only"}


def test_position_snapshot_includes_close_snapshot(client_with_data):
    repo = client_with_data.application.config["REPO"]
    rows = client_with_data.get("/api/review/closed_positions").get_json()["positions"]
    pid = rows[0]["id"]
    repo.save_position_close_snapshot(pid, {
        "schema": "position_close_snapshot_v1",
        "closed_at": rows[0].get("close_at"),
        "close_premium": 0.5,
        "selected_close_reason": "take_profit_50",
        "exit_signal": {"reason_text": "捕获约 55% 最大收益"},
        "mark": {"spot": 180.0, "delta": -0.08, "iv": 0.25},
    }, None)
    r = client_with_data.get(f"/api/review/positions/{pid}/snapshot")
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("close_snapshot") is not None
    assert body["close_snapshot"]["close_premium"] == 0.5


def test_position_diagnosis_endpoint(client_with_data):
    repo = client_with_data.application.config["REPO"]
    rows = client_with_data.get("/api/review/closed_positions").get_json()["positions"]
    pid = next(r["id"] for r in rows if r["symbol"] == "TSLA")
    repo.save_open_snapshot(pid, {
        "quality_grade": "A",
        "entry_signal_status": "OPENABLE",
        "delta": -0.15,
        "dte": 30,
        "iv_rank": 50,
        "margin_buffer": 0.12,
    })
    r = client_with_data.get(f"/api/review/positions/{pid}/diagnosis")
    assert r.status_code == 200
    body = r.get_json()
    assert body["position_id"] == pid
    assert len(body["dimension_summary"]) == 9
    assert any("止盈" in h.get("text", "") for h in body.get("highlights", []))


def test_position_diagnosis_rejects_open(client_with_data):
    repo = client_with_data.application.config["REPO"]
    pid = repo.insert_position({
        "symbol": "TEST",
        "expiration": "2026-12-19",
        "strike": 100.0,
        "contracts": 1,
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_premium": 2.0,
        "state": "OPEN",
    })
    r = client_with_data.get(f"/api/review/positions/{pid}/diagnosis")
    assert r.status_code == 400


@patch("app.api.routes_review.time.sleep")
@patch("app.api.routes_review.build_open_snapshot_dict")
def test_refresh_entry_snapshots_includes_open_positions(mock_build, _sleep, client_with_data):
    mock_build.return_value = {"rsi_6": 44.0}
    repo = client_with_data.application.config["REPO"]
    open_pid = repo.insert_position({
        "symbol": "OPENZ",
        "expiration": "2026-07-18",
        "strike": 100.0,
        "contracts": 1,
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_premium": 1.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    resp = client_with_data.post("/api/review/snapshots/refresh_entry")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == 4
    assert body["saved"] == 4
    assert mock_build.call_count == 4
    snap = client_with_data.get(f"/api/review/positions/{open_pid}/snapshot").get_json()
    assert snap["open_snapshot"]["rsi_6"] == pytest.approx(44.0)


@patch("app.api.routes_review.time.sleep")
@patch("app.api.routes_review.build_open_snapshot_dict")
def test_refresh_closed_snapshots_writes_each_row(mock_build, _sleep, client_with_data):
    mock_build.return_value = {"rsi_6": 44.0}
    resp = client_with_data.post("/api/review/snapshots/refresh_closed")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == 3
    assert body["saved"] == 3
    assert mock_build.call_count == 3


@patch("app.api.routes_review.build_open_snapshot_dict")
def test_refresh_snapshot_accepts_open_position(mock_build, tmp_path):
    mock_build.return_value = {"rsi_6": 50.0}
    db_path = tmp_path / "open_only.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    repo = Repo(db_path)
    pid = repo.insert_position({
        "symbol": "AAPL",
        "expiration": "2026-06-20",
        "strike": 150.0,
        "contracts": 1,
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_premium": 2.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    with app.test_client() as c:
        resp = c.post(f"/api/review/positions/{pid}/snapshot/refresh")
        assert resp.status_code == 200
        snap = c.get(f"/api/review/positions/{pid}/snapshot").get_json()
        assert snap["open_snapshot"]["rsi_6"] == pytest.approx(50.0)


@patch("app.api.routes_review.build_open_snapshot_dict")
def test_refresh_single_closed_snapshot(mock_build, client_with_data):
    mock_build.return_value = {"rsi_12": 41.0}
    rows = client_with_data.get("/api/review/closed_positions").get_json()["positions"]
    pid = rows[0]["id"]
    resp = client_with_data.post(f"/api/review/positions/{pid}/snapshot/refresh")
    assert resp.status_code == 200
    snap = client_with_data.get(f"/api/review/positions/{pid}/snapshot").get_json()
    assert snap["open_snapshot"]["rsi_12"] == pytest.approx(41.0)


def test_attribution_finds_spot_close_when_close_at_space_format_breaks_string_sort(tmp_path):
    """Regression: lexicographic compare of ISO-with-T vs space made spot_close None."""
    import json

    db_path = tmp_path / "attr.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    repo = Repo(db_path)

    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-05-22",
        "strike": 680.0,
        "contracts": 1,
        "open_at": "2026-05-10T14:00:00+00:00",
        "open_premium": 5.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.save_open_snapshot(pid, {"spot": 720.0, "delta": -0.13, "theta": -0.05})
    repo.insert_radar_snapshot({
        "position_id": pid,
        "taken_at": "2026-05-14T15:30:00+00:00",
        "spot": 710.0,
        "current_mid": 2.0,
        "pnl_pct": 0.656,
        "delta": -0.12,
        "margin_buffer": 0.08,
        "signals": json.dumps([]),
    })
    repo.close_position(
        pid, "CLOSED_EARLY", 2.5, "manual", 250.0,
        close_at="2026-05-14 16:00:00+00:00",
    )

    with app.test_client() as c:
        r = c.get(f"/api/review/positions/{pid}/attribution")
        assert r.status_code == 200
        data = r.get_json()
        assert data["spot_close"] == pytest.approx(710.0)
        assert data["delta_contribution"] is not None
        assert data["theta_contribution"] is not None


def test_merge_open_snapshot_refresh_skips_none_from_built(monkeypatch, tmp_path):
    db_path = tmp_path / "merge.db"
    init_database(db_path)
    repo = Repo(db_path)
    pid = repo.insert_position({
        "symbol": "X",
        "expiration": "2026-08-01",
        "strike": 100.0,
        "contracts": 1,
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_premium": 1.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.save_open_snapshot(pid, {"iv": 0.45, "delta": -0.2})

    from app.api.routes_review import _merge_open_snapshot_refresh

    monkeypatch.setattr(
        "app.api.routes_review.build_open_snapshot_dict",
        lambda *_a, **_k: {"rsi_6": 83.1, "iv": None},
    )
    pos = repo.get_position(pid)
    merged = _merge_open_snapshot_refresh(repo, pid, pos)
    assert merged["iv"] == pytest.approx(0.45)
    assert merged["rsi_6"] == pytest.approx(83.1)


def test_review_entry_recalc_rejects_open(tmp_path):
    db_path = tmp_path / "recalc_open.db"
    init_database(db_path)
    repo = Repo(db_path)
    pid = repo.insert_position({
        "symbol": "ZZZ",
        "expiration": "2026-12-19",
        "strike": 50.0,
        "contracts": 1,
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_premium": 2.5,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.post(f"/api/review/positions/{pid}/entry_recalc")
    assert r.status_code == 422
    j = r.get_json()
    assert j.get("ok") is False
    assert "closed" in str(j.get("error") or "").lower()


def test_review_bulk_recalc_closed_insights_removed(tmp_path):
    db_path = tmp_path / "bulk_gone.db"
    init_database(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.post("/api/review/snapshots/recalc_closed_insights")
    assert r.status_code == 404


def test_review_attribution_mae_mfe_uses_et_calendar_not_instant_midday_close(tmp_path):
    """
    Synthetic replay stamps ~16:00 ET; intraday close on the **same ET date**
    must not discard all pnl_pct (previous bug yielded empty MAE/MFE).
    """
    db_path = tmp_path / "attr_maemfe_cal.db"
    init_database(db_path)
    repo = Repo(db_path)
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-06-18",
        "strike": 480.0,
        "contracts": 1,
        "open_at": "2026-06-11T10:00:00-04:00",
        "open_premium": 10.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(
        pid,
        "CLOSED_EARLY",
        5.0,
        "manual",
        499.0,
        close_at="2026-06-13T14:00:00-04:00",
    )
    repo.save_open_snapshot(pid, {"spot": 230.0, "delta": -0.22, "theta": -0.05, "iv": 0.4})

    repo.insert_radar_snapshot({
        "position_id": pid,
        "taken_at": "2026-06-11T16:00:00-04:00",
        "spot": 229.0,
        "current_mid": 11.5,
        "pnl_pct": -0.15,
        "delta": -0.2,
        "margin_buffer": 0.06,
        "signals": '["synthetic_replay"]',
    })
    repo.insert_radar_snapshot({
        "position_id": pid,
        "taken_at": "2026-06-13T16:00:00-04:00",
        "spot": 228.0,
        "current_mid": 6.5,
        "pnl_pct": 0.35,
        "delta": -0.18,
        "margin_buffer": 0.05,
        "signals": '["synthetic_replay"]',
    })

    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get(f"/api/review/positions/{pid}/attribution")

    assert r.status_code == 200
    j = r.get_json()
    # Excursion versus first chronological snapshot (replay entry bar), not global min/max.
    assert j.get("mae") == pytest.approx(0.0)
    assert j.get("mfe") == pytest.approx(0.5)
    assert j.get("mae_mfe_flat_replay") is False
    assert j.get("radar_points") == 2


def test_review_attribution_mae_mfe_null_when_flat_replay_pnls(tmp_path):
    db_path = tmp_path / "attr_flat_maemfe.db"
    init_database(db_path)
    repo = Repo(db_path)
    pid = repo.insert_position({
        "symbol": "SPY",
        "expiration": "2026-12-19",
        "strike": 400.0,
        "contracts": 1,
        "open_at": "2026-06-10T15:30:00+00:00",
        "open_premium": 2.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(
        pid,
        "CLOSED_EARLY",
        1.0,
        "manual",
        50.0,
        close_at="2026-06-14T14:30:00+00:00",
    )
    repo.save_open_snapshot(pid, {"spot": 400.0, "delta": -0.1, "theta": -0.02, "iv": 0.2})
    for day in ("2026-06-10", "2026-06-12"):
        repo.insert_radar_snapshot({
            "position_id": pid,
            "taken_at": f"{day}T16:00:00-04:00",
            "spot": 400.0,
            "current_mid": 2.0,
            "pnl_pct": 0.0,
            "delta": -0.1,
            "margin_buffer": 0.01,
            "signals": '["synthetic_replay"]',
        })

    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get(f"/api/review/positions/{pid}/attribution")
    assert r.status_code == 200
    j = r.get_json()
    assert j.get("mae") is None
    assert j.get("mfe") is None
    assert j.get("mae_mfe_flat_replay") is True


def test_review_snapshot_migrates_stale_intraday_bs_on_get(tmp_path, monkeypatch):
    """Legacy 5m intraday_bs persists in DB; GET snapshot should rewrite to daily HL."""
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    db_path = tmp_path / "mig.db"
    init_database(db_path)
    repo = Repo(db_path)
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-06-18",
        "strike": 480.0,
        "contracts": 1,
        "open_at": "2026-05-06T14:30:00+00:00",
        "open_premium": 10.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 5.0, "manual", 100.0,
                        close_at="2026-05-07T18:00:00+00:00")
    repo.save_open_snapshot(pid, {
        "iv": 0.35,
        "intraday_bs": {
            "source": "intraday_bs",
            "model": "5m_stock_bs_eod_iv",
            "interval": "5m",
            "bar_count": 156,
            "option_ticker": "PLACEHOLDER",
            "mae_pnl_pct": -0.075,
            "mfe_pnl_pct": 0.014,
        },
    })

    app = create_app(db_path=db_path)
    app.config["TESTING"] = True

    with patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl") as m_hl, patch(
        "app.core.pnl_excursion_intraday._fetch_eod_iv_map", return_value={}
    ):
        m_hl.return_value = [
            (date(2026, 5, 6), 100.0, 102.0),
            (date(2026, 5, 7), 101.0, 103.0),
        ]
        with app.test_client() as c:
            r = c.get(f"/api/review/positions/{pid}/snapshot")

    assert r.status_code == 200
    bs = (r.get_json().get("open_snapshot") or {}).get("intraday_bs")
    assert bs is not None
    assert bs["model"] == "daily_hl_bs_eod_iv"
    assert bs["interval"] == "1d_hl"
    assert bs["bar_count"] == 4


def test_review_snapshot_skips_migration_when_intraday_bs_current(tmp_path, monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    db_path = tmp_path / "skip.db"
    init_database(db_path)
    repo = Repo(db_path)
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-06-18",
        "strike": 480.0,
        "contracts": 1,
        "open_at": "2026-05-06T14:30:00+00:00",
        "open_premium": 10.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 5.0, "manual", 100.0,
                        close_at="2026-05-07T18:00:00+00:00")
    repo.save_open_snapshot(pid, {
        "iv": 0.35,
        "intraday_bs": {
            "model": "daily_hl_bs_eod_iv",
            "interval": "1d_hl",
            "bar_count": 4,
            "option_ticker": "O:MU260618P00480000",
            "mae_pnl_pct": -0.1,
            "mfe_pnl_pct": 0.02,
        },
    })

    app = create_app(db_path=db_path)
    app.config["TESTING"] = True

    with patch(
        "app.api.routes_review.enrich_closed_position_intraday_bs"
    ) as m_enrich:
        with app.test_client() as c:
            r = c.get(f"/api/review/positions/{pid}/snapshot")

    assert r.status_code == 200
    m_enrich.assert_not_called()


def test_review_snapshot_migrates_same_et_day_intraday_bs_still_at_1d_hl(tmp_path, monkeypatch):
    """Stale same-day blobs from before hold-window clipping must re-enrich."""
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    db_path = tmp_path / "same_day.db"
    init_database(db_path)
    repo = Repo(db_path)
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-05-08",
        "strike": 600.0,
        "contracts": 1,
        "open_at": "2026-05-07T18:05:00+00:00",
        "open_premium": 2.08,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 0.52, "manual", 50.0,
                        close_at="2026-05-07T18:06:00+00:00")
    repo.save_open_snapshot(pid, {
        "iv": 0.35,
        "spot": 646.63,
        "intraday_bs": {
            "model": "daily_hl_bs_eod_iv",
            "interval": "1d_hl",
            "hold_window": {
                "open_date_et": "2026-05-07",
                "close_date_et": "2026-05-07",
            },
            "bar_count": 2,
            "option_ticker": "O:MU260508P00600000",
            "mae_pnl_pct": 0.0,
            "mfe_pnl_pct": 0.971066,
        },
    })

    app = create_app(db_path=db_path)
    app.config["TESTING"] = True

    with patch("app.api.routes_review.enrich_closed_position_intraday_bs") as m_enrich:
        with app.test_client() as c:
            r = c.get(f"/api/review/positions/{pid}/snapshot")

    assert r.status_code == 200
    m_enrich.assert_called_once()


def test_review_snapshot_skips_migration_same_et_day_explicit_fallback(tmp_path, monkeypatch):
    """Confirmed hold-window scrape failed → do not endlessly re-run."""
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    db_path = tmp_path / "same_day_fallback.db"
    init_database(db_path)
    repo = Repo(db_path)
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-05-08",
        "strike": 600.0,
        "contracts": 1,
        "open_at": "2026-05-07T18:05:00+00:00",
        "open_premium": 2.08,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 0.52, "manual", 50.0,
                        close_at="2026-05-07T18:06:00+00:00")
    repo.save_open_snapshot(pid, {
        "iv": 0.35,
        "spot": 646.63,
        "intraday_bs": {
            "model": "daily_hl_bs_eod_iv",
            "interval": "1d_hl",
            "hold_window_fallback": True,
            "hold_window": {
                "open_date_et": "2026-05-07",
                "close_date_et": "2026-05-07",
            },
            "bar_count": 2,
            "option_ticker": "O:MU260508P00600000",
        },
    })

    app = create_app(db_path=db_path)
    app.config["TESTING"] = True

    with patch("app.api.routes_review.enrich_closed_position_intraday_bs") as m_enrich:
        with app.test_client() as c:
            r = c.get(f"/api/review/positions/{pid}/snapshot")

    assert r.status_code == 200
    m_enrich.assert_not_called()


def test_review_snapshot_skips_migration_when_intraday_bs_hold_window(tmp_path, monkeypatch):
    """hold_window_hl is a current interval; snapshot GET should not re-enrich."""
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    db_path = tmp_path / "skip_hw.db"
    init_database(db_path)
    repo = Repo(db_path)
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-06-18",
        "strike": 480.0,
        "contracts": 1,
        "open_at": "2026-05-06T14:30:00+00:00",
        "open_premium": 10.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 5.0, "manual", 100.0,
                        close_at="2026-05-07T18:00:00+00:00")
    repo.save_open_snapshot(pid, {
        "iv": 0.35,
        "intraday_bs": {
            "model": "daily_hl_bs_eod_iv",
            "interval": "hold_window_hl",
            "bar_count": 2,
            "option_ticker": "O:MU260618P00480000",
            "mae_pnl_pct": -0.1,
            "mfe_pnl_pct": 0.02,
        },
    })

    app = create_app(db_path=db_path)
    app.config["TESTING"] = True

    with patch(
        "app.api.routes_review.enrich_closed_position_intraday_bs"
    ) as m_enrich:
        with app.test_client() as c:
            r = c.get(f"/api/review/positions/{pid}/snapshot")

    assert r.status_code == 200
    m_enrich.assert_not_called()


def test_summary_avg_maee_mfe_prefers_intraday_bs_over_radar_snapshots(tmp_path):
    """
    avg_maee / avg_mfe in summary should use intraday_bs.mae_pnl_pct /
    mfe_pnl_pct (daily H/L × BS, same as the drawer shows) when available,
    and fall back to radar_snapshots MIN/MAX only when intraday_bs is absent.

    Setup:
    - p1: radar pnl_pct [-0.10, +0.20], intraday_bs mae=-0.25 mfe=+0.35
      → contribution to avg: intraday_bs values (-0.25, +0.35)
    - p2: radar pnl_pct [-0.05, +0.10], no intraday_bs
      → contribution to avg: radar values (-0.05, +0.10)

    Expected avg_maee = (-0.25 + -0.05) / 2 = -0.15
    Expected avg_mfe  = (+0.35 + +0.10) / 2 = +0.225
    """
    db_path = tmp_path / "intraday_pref.db"
    init_database(db_path)
    repo = Repo(db_path)

    def _closed(symbol, strike):
        pid = repo.insert_position({
            "symbol": symbol,
            "expiration": "2026-09-19",
            "strike": strike,
            "contracts": 1,
            "open_at": "2026-05-01T14:30:00+00:00",
            "open_premium": 2.0,
            "open_candidate_id": None,
            "state": "OPEN",
            "notes": None,
        })
        repo.close_position(pid, "CLOSED_EARLY", 0.5, "manual", 100.0,
                            close_at="2026-05-10T14:30:00+00:00")
        return pid

    def _snap(pid, pnl_pct, taken_suffix):
        repo.insert_radar_snapshot({
            "position_id": pid,
            "taken_at": f"2026-05-0{taken_suffix}T16:00:00+00:00",
            "spot": 300.0,
            "current_mid": 2.0 * (1 - pnl_pct),
            "pnl_pct": pnl_pct,
            "delta": -0.15,
            "margin_buffer": 0.1,
            "signals": '["synthetic_replay"]',
        })

    # p1 — has intraday_bs with deeper MAE than EOD radar captures
    p1 = _closed("AAPL", 150.0)
    _snap(p1, -0.10, "2")  # radar min
    _snap(p1, 0.20, "8")   # radar max
    repo.save_open_snapshot(p1, {
        "iv": 0.30,
        "intraday_bs": {
            "model": "daily_hl_bs_eod_iv",
            "interval": "1d_hl",
            "bar_count": 4,
            "option_ticker": "O:AAPL260919P00150000",
            "mae_pnl_pct": -0.25,   # worse than EOD radar (-0.10)
            "mfe_pnl_pct": 0.35,    # better than EOD radar (0.20)
        },
    })

    # p2 — no intraday_bs; summary must fall back to radar_snapshot MIN/MAX
    p2 = _closed("TSLA", 200.0)
    _snap(p2, -0.05, "3")  # radar min
    _snap(p2, 0.10, "7")   # radar max

    app = create_app(db_path=db_path)
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get("/api/review/summary")

    assert r.status_code == 200
    data = r.get_json()

    assert data["avg_maee"] == pytest.approx(-0.15, abs=0.001)
    assert data["avg_mfe"] == pytest.approx(0.225, abs=0.001)


def test_review_summary_includes_open_unrealized_and_total_pnl(tmp_path):
    db_path = tmp_path / "open_pnl.db"
    init_database(db_path)
    repo = Repo(db_path)
    app = create_app(db_path=db_path)
    app.config["TESTING"] = True

    closed_id = repo.insert_position({
        "symbol": "AAPL",
        "expiration": "2026-06-20",
        "strike": 150.0,
        "contracts": 1,
        "open_at": "2026-05-01T14:30:00+00:00",
        "open_premium": 2.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(closed_id, "CLOSED_EARLY", 1.0, "manual", 100.0,
                        close_at="2026-05-10T14:30:00+00:00")

    open_id = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-06-18",
        "strike": 600.0,
        "contracts": 1,
        "open_at": "2026-05-21T13:43:00+00:00",
        "open_premium": 16.5,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.insert_exit_signal({
        "schema": "exit_signal_v1",
        "position_id": open_id,
        "action": "HOLD",
        "severity": "info",
        "urgency_score": 12,
        "summary": "hold",
        "metrics": {"unrealized_pnl_usd": 70.0},
        "generated_at": "2026-05-21T15:00:00+00:00",
    })

    with app.test_client() as c:
        data = c.get("/api/review/summary").get_json()

    assert data["total_realized_pnl"] == pytest.approx(100.0)
    assert data["total_unrealized_pnl"] == pytest.approx(70.0)
    assert data["total_pnl"] == pytest.approx(170.0)
    assert data["open_position_count"] == 1


def test_compute_sortino_ratio_single_downside():
    from app.api.routes_review import _compute_sortino_ratio

    roes = [0.01, 0.02, -0.007]
    sortino = _compute_sortino_ratio(roes)
    assert sortino is not None
    assert sortino == pytest.approx(statistics.mean(roes) / 0.007, rel=0.01)


def test_compute_sortino_ratio_no_downside():
    from app.api.routes_review import _compute_sortino_ratio

    assert _compute_sortino_ratio([0.01, 0.02, 0.03]) is None
