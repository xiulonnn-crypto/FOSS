from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.core.open_snapshot import build_open_snapshot_dict
from app.db.init_db import init_database
from app.db.repo import Repo


def _candidate_row(scan_run_id: int):
    return {
        "scan_run_id": scan_run_id,
        "symbol": "AAPL",
        "expiration": str(date.today() + timedelta(days=35)),
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
        "quality_grade": "B",
        "quality_score": 85,
        "quality_flags": ["greeks_bs_fallback"],
        "quote_age_seconds": 900,
        "greeks_source": "bs_fallback",
        "iv_rank_source": "rv_proxy",
    }


def test_open_snapshot_merges_quality_fields_from_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.open_snapshot.closes_through_entry", lambda *_a, **_kw: None)
    db_path = tmp_path / "quality_snapshot.db"
    init_database(db_path)
    repo = Repo(db_path)
    rid = repo.insert_scan_run(provider="fake", trigger="test")
    repo.insert_candidates([_candidate_row(rid)])
    cand = repo.list_candidates(rid)[0]

    pos = {
        "symbol": "AAPL",
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_candidate_id": cand["id"],
    }
    snap = build_open_snapshot_dict(repo, pos, None)
    assert snap["quality_grade"] == "B"
    assert snap["quality_score"] == 85
    assert snap["quality_flags"] == ["greeks_bs_fallback"]
    assert snap["greeks_source"] == "bs_fallback"


def test_open_snapshot_merges_inline_quality_fields(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.open_snapshot.closes_through_entry", lambda *_a, **_kw: None)
    db_path = tmp_path / "quality_snapshot_inline.db"
    init_database(db_path)
    repo = Repo(db_path)
    pos = {
        "symbol": "AAPL",
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_candidate_id": None,
    }
    req = {
        "quality_grade": "A",
        "quality_score": 100,
        "quality_flags": ["provider_delayed"],
        "quote_age_seconds": 900,
        "greeks_source": "provider",
        "iv_rank_source": "rv_proxy",
    }
    snap = build_open_snapshot_dict(repo, pos, req)
    assert snap["quality_grade"] == "A"
    assert snap["quality_score"] == 100
    assert snap["quality_flags"] == ["provider_delayed"]
    assert snap["quote_age_seconds"] == 900


def test_open_snapshot_merges_pool_reference_fields(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.open_snapshot.closes_through_entry", lambda *_a, **_kw: None)
    db_path = tmp_path / "quality_snapshot_pool.db"
    init_database(db_path)
    repo = Repo(db_path)
    pos = {
        "symbol": "AAPL",
        "open_at": datetime.now(timezone.utc).isoformat(),
        "open_candidate_id": None,
    }
    req = {
        "option_pool_id": 12,
        "option_watchlist_id": "34",
    }

    snap = build_open_snapshot_dict(repo, pos, req)

    assert snap["option_pool_id"] == 12
    assert snap["option_watchlist_id"] == 34
