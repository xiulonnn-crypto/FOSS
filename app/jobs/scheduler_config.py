from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor

if TYPE_CHECKING:
    from app.db.repo import Repo

log = logging.getLogger(__name__)

_JOBSTORE_URL = "sqlite:///data/options.db"


def build_scheduler(repo: "Repo") -> BackgroundScheduler:
    """Create a BackgroundScheduler with SQLAlchemy job store."""
    jobstores = {
        "default": SQLAlchemyJobStore(url=_JOBSTORE_URL),
    }
    executors = {
        "default": ThreadPoolExecutor(max_workers=4),
    }
    job_defaults = {
        "misfire_grace_time": 300,
        "coalesce": True,
    }
    scheduler = BackgroundScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=job_defaults,
    )
    return scheduler


def register_jobs(scheduler: BackgroundScheduler, repo: "Repo") -> None:
    """Register all periodic jobs. Safe to call multiple times (reschedule)."""
    _remove_managed_jobs(scheduler)

    settings = repo.get_settings()
    schedules = settings.get("schedules", {})

    screener_cron = schedules.get("screener", "0 9 * * 1-5")
    radar_cron = schedules.get("radar", "*/15 9-16 * * 1-5")
    settlement_cron = schedules.get("settlement", "35 16 * * 1-5")
    iv_history_cron = schedules.get("iv_history", "0 17 * * 1-5")

    _add_cron_job(scheduler, "job_screener", screener_cron, repo)
    _add_cron_job(scheduler, "job_radar", radar_cron, repo)
    _add_cron_job(scheduler, "job_settlement", settlement_cron, repo)
    _add_cron_job(scheduler, "job_iv_history", iv_history_cron, repo)

    log.info("scheduler: registered %d jobs", len(scheduler.get_jobs()))


def _remove_managed_jobs(scheduler: BackgroundScheduler) -> None:
    managed = ["job_screener", "job_radar", "job_settlement", "job_iv_history"]
    for job_id in managed:
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass


def _add_cron_job(
    scheduler: BackgroundScheduler,
    job_id: str,
    cron_expr: str,
    repo: "Repo",
) -> None:
    """Parse a 5-field cron expression and add it as an APScheduler cron job."""
    parts = cron_expr.split()
    if len(parts) != 5:
        log.warning("scheduler: invalid cron '%s' for %s, skipping", cron_expr, job_id)
        return

    minute, hour, day, month, day_of_week = parts
    func = _NOOP_MAP.get(job_id)
    if func is None:
        log.warning("scheduler: no callable found for %s, skipping", job_id)
        return

    try:
        scheduler.add_job(
            func,
            trigger="cron",
            id=job_id,
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            replace_existing=True,
        )
    except Exception as exc:
        log.error("scheduler: failed to add %s: %s", job_id, exc)


def _noop_screener():
    log.debug("scheduler: noop tick for job_screener")


def _noop_radar():
    log.debug("scheduler: noop tick for job_radar")


def _noop_settlement():
    log.debug("scheduler: noop tick for job_settlement")


def _noop_iv_history():
    log.debug("scheduler: noop tick for job_iv_history")


_NOOP_MAP = {
    "job_screener": _noop_screener,
    "job_radar": _noop_radar,
    "job_settlement": _noop_settlement,
    "job_iv_history": _noop_iv_history,
}
