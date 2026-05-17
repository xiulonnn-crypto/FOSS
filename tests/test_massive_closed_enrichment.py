"""Closed-position Massive enrichment merge."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.core.massive_closed_enrichment import enrich_closed_position_open_snapshot_massive
from app.db.init_db import init_database
from app.db.repo import Repo


@pytest.fixture
def repo(tmp_path):
    p = tmp_path / "m.db"
    init_database(p)
    return Repo(p)


def _insert_closed(repo: Repo) -> int:
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-08-01",
        "strike": 100.0,
        "contracts": 1,
        "open_at": "2026-05-10T15:30:00+00:00",
        "open_premium": 3.2,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(
        pid,
        "CLOSED_EARLY",
        1.5,
        "manual",
        170.0,
        close_at="2026-05-13T20:05:00+00:00",
    )
    repo.save_open_snapshot(pid, {"spot": 118.0, "iv": 0.3})
    return pid


def test_enrich_skips_without_flag(repo, monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    pid = _insert_closed(repo)
    enrich_closed_position_open_snapshot_massive(repo, pid)
    snap = repo.get_open_snapshot(pid) or {}
    assert "massive" not in snap


def test_enrich_skips_without_api_key(repo, monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    repo.merge_settings({"integrations": {"massive_enrich_closed": 1}})
    pid = _insert_closed(repo)
    enrich_closed_position_open_snapshot_massive(repo, pid)
    snap = repo.get_open_snapshot(pid) or {}
    assert "massive" not in snap


@patch("app.core.massive_closed_enrichment.MassiveClient")
def test_enrich_merges_massive_block(mock_client_cls, repo, monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "secret")
    repo.merge_settings({"integrations": {"massive_enrich_closed": 1}})
    pid = _insert_closed(repo)

    inst = MagicMock()
    inst.configured = True
    inst.fetch_daily_aggs.return_value = [
        {"t": int(datetime(2026, 5, 10, 16, 0, tzinfo=timezone.utc).timestamp() * 1000), "c": 3.0},
        {"t": int(datetime(2026, 5, 11, 16, 0, tzinfo=timezone.utc).timestamp() * 1000), "c": 2.5},
    ]
    mock_client_cls.return_value = inst

    enrich_closed_position_open_snapshot_massive(repo, pid)
    snap = repo.get_open_snapshot(pid) or {}
    assert snap.get("spot") == 118.0
    m = snap.get("massive")
    assert m is not None
    assert m["option_ticker"].startswith("O:MU")
    assert m.get("hold_window") == {
        "open_date_et": "2026-05-10",
        "close_date_et": "2026-05-13",
    }
    assert m.get("fetch_clip", {}).get("start_date_et") == "2026-05-10"
    assert m.get("fetch_clip", {}).get("end_date_et") == "2026-05-13"
    # Excursion versus first ET session in hold window (consistent with attribution MAE/MFE).
    assert m["mae_pnl_pct"] == pytest.approx(0.0)
    assert m["mfe_pnl_pct"] == pytest.approx(0.15625)


@patch("app.core.massive_closed_enrichment.MassiveClient")
def test_enrich_env_true_overrides_settings_false(mock_client_cls, repo, monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "secret")
    monkeypatch.setenv("MASSIVE_ENRICH_CLOSED", "1")
    repo.merge_settings({"integrations": {"massive_enrich_closed": 0}})
    pid = _insert_closed(repo)
    inst = MagicMock()
    inst.configured = True
    inst.fetch_daily_aggs.return_value = [
        {"t": int(datetime(2026, 5, 10, 16, 0, tzinfo=timezone.utc).timestamp() * 1000), "c": 3.0},
    ]
    mock_client_cls.return_value = inst
    enrich_closed_position_open_snapshot_massive(repo, pid)
    snap = repo.get_open_snapshot(pid) or {}
    assert snap.get("massive") is not None
    m = snap["massive"]
    assert m["bar_count"] == 1
    assert m.get("mae_pnl_pct") is None
    assert m.get("mfe_pnl_pct") is None


def test_enrich_env_false_overrides_settings_true(repo, monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "secret")
    monkeypatch.setenv("MASSIVE_ENRICH_CLOSED", "0")
    repo.merge_settings({"integrations": {"massive_enrich_closed": 1}})
    pid = _insert_closed(repo)
    enrich_closed_position_open_snapshot_massive(repo, pid)
    snap = repo.get_open_snapshot(pid) or {}
    assert "massive" not in snap
