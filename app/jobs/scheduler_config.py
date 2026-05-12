from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.db.repo import Repo

log = logging.getLogger(__name__)

DB_URL = "sqlite:///data/options.db"
_TRADING_DAYS = "mon-fri"
_ET_TZ = "America/New_York"


def build_scheduler(repo: Repo, db_url: str = DB_URL) -> BackgroundScheduler:
    """Create and configure APScheduler with SQLAlchemyJobStore."""
    jobstores = {"default": SQLAlchemyJobStore(url=db_url)}
    scheduler = BackgroundScheduler(
        jobstores=jobstores,
        timezone=_ET_TZ,
    )
    return scheduler


def register_jobs(scheduler: BackgroundScheduler, repo: Repo) -> None:
    """Register all recurring jobs from current settings."""
    from app.data.provider_yfinance import YFinanceProvider
    from app.jobs.job_screener import run_screener
    from app.jobs.job_radar import run_radar
    from app.jobs.job_settlement import run_settlement
    from app.jobs.job_iv_history import run_iv_history

    settings = repo.get_settings()
    schedule = settings.get("schedule", {})
    screener_min = int(schedule.get("screener_minutes", 15))
    radar_min = int(schedule.get("radar_minutes", 15))
    settle_time = schedule.get("settlement_time_et", "16:30")
    iv_time = schedule.get("iv_refresh_time_et", "17:00")

    db_path = Path("data/options.db")
    risk_free_rate = float(settings.get("risk_free_rate", 0.045))

    def _screener():
        r = Repo(db_path)
        p = YFinanceProvider()
        run_screener(r, p, trigger="scheduled", risk_free_rate=risk_free_rate)

    def _radar():
        r = Repo(db_path)
        p = YFinanceProvider()
        run_radar(r, p, risk_free_rate=risk_free_rate)

    def _settlement():
        r = Repo(db_path)
        p = YFinanceProvider()
        run_settlement(r, p)

    def _iv_history():
        r = Repo(db_path)
        p = YFinanceProvider()
        run_iv_history(r, p)

    settle_h, settle_m = (int(x) for x in settle_time.split(":"))
    iv_h, iv_m = (int(x) for x in iv_time.split(":"))

    # Remove existing jobs to allow re-registration
    for jid in ["screener", "radar", "settlement", "iv_history"]:
        try:
            scheduler.remove_job(jid)
        except Exception:
            pass

    scheduler.add_job(
        _screener,
        IntervalTrigger(minutes=screener_min, timezone=_ET_TZ),
        id="screener",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        _radar,
        IntervalTrigger(minutes=radar_min, timezone=_ET_TZ),
        id="radar",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        _settlement,
        CronTrigger(
            day_of_week=_TRADING_DAYS,
            hour=settle_h,
            minute=settle_m,
            timezone=_ET_TZ,
        ),
        id="settlement",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        _iv_history,
        CronTrigger(
            day_of_week=_TRADING_DAYS,
            hour=iv_h,
            minute=iv_m,
            timezone=_ET_TZ,
        ),
        id="iv_history",
        replace_existing=True,
        misfire_grace_time=300,
    )
    log.info(
        "scheduler: jobs registered (screener=%dm, radar=%dm, settle=%s, iv=%s)",
        screener_min, radar_min, settle_time, iv_time,
    )
