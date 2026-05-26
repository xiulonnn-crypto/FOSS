from __future__ import annotations

from pathlib import Path

import pytest

from app.db.init_db import init_database
from app.db.repo import Repo
from app.jobs.scheduler_config import build_scheduler, register_jobs


@pytest.fixture
def scheduler_repo(tmp_path: Path):
    db = tmp_path / "sched.db"
    init_database(db)
    repo = Repo(db)
    sched = build_scheduler(repo)
    yield repo, sched
    try:
        sched.shutdown(wait=False)
    except Exception:
        pass


def test_register_jobs_omits_screener_when_disabled(scheduler_repo):
    repo, sched = scheduler_repo
    s = repo.get_settings()
    s.setdefault("schedule", {})["screener_minutes"] = 0
    repo.save_settings(s)
    register_jobs(sched, repo)
    ids = {j.id for j in sched.get_jobs()}
    assert "screener" not in ids
    assert "radar" in ids


def test_register_jobs_registers_screener_when_interval_positive(scheduler_repo):
    repo, sched = scheduler_repo
    s = repo.get_settings()
    s.setdefault("schedule", {})["screener_minutes"] = 7
    repo.save_settings(s)
    register_jobs(sched, repo)
    ids = {j.id for j in sched.get_jobs()}
    assert "screener" in ids


def test_register_jobs_accepts_legacy_integer_wall_clock_hours(scheduler_repo):
    repo, sched = scheduler_repo
    s = repo.get_settings()
    s.setdefault("schedule", {})["settlement_time_et"] = 16
    s.setdefault("schedule", {})["iv_refresh_time_et"] = 17
    repo.save_settings(s)

    register_jobs(sched, repo)

    jobs = {j.id: j for j in sched.get_jobs()}
    assert "settlement" in jobs
    assert "iv_history" in jobs


def test_register_jobs_adds_iv_snapshot_after_iv_history(scheduler_repo):
    repo, sched = scheduler_repo

    register_jobs(sched, repo)

    ids = {j.id for j in sched.get_jobs()}
    assert "iv_history" in ids
    assert "iv_snapshot" in ids


def test_default_settings_have_scheduled_screener_disabled(scheduler_repo):
    repo, _sched = scheduler_repo
    assert repo.get_settings().get("schedule", {}).get("screener_minutes") == 0
