from __future__ import annotations

import logging
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.db.paths import options_db_path
from app.db.repo import Repo

log = logging.getLogger(__name__)

_TRADING_DAYS = "mon-fri"
_ET_TZ = "America/New_York"


# Module-level callables so SQLAlchemyJobStore can pickle job references.


def job_screener_tick() -> None:
    from app.data.provider_yfinance import YFinanceProvider
    from app.jobs.job_screener import run_screener

    r = Repo(options_db_path())
    settings = r.get_settings()
    risk_free = float(settings.get("risk_free_rate", 0.045))
    run_screener(r, YFinanceProvider(), trigger="scheduled", risk_free_rate=risk_free)


def job_radar_tick() -> None:
    from app.data.provider_yfinance import YFinanceProvider
    from app.jobs.job_radar import run_radar

    r = Repo(options_db_path())
    settings = r.get_settings()
    risk_free = float(settings.get("risk_free_rate", 0.045))
    run_radar(r, YFinanceProvider(), risk_free_rate=risk_free)


def job_settlement_tick() -> None:
    from app.data.provider_yfinance import YFinanceProvider
    from app.jobs.job_settlement import run_settlement

    run_settlement(Repo(options_db_path()), YFinanceProvider())


def job_iv_history_tick() -> None:
    from app.data.provider_yfinance import YFinanceProvider
    from app.jobs.job_iv_history import run_iv_history

    run_iv_history(Repo(options_db_path()), YFinanceProvider())


def job_option_pool_maintenance_tick() -> None:
    from app.jobs.job_screener import run_option_pool_maintenance

    run_option_pool_maintenance(Repo(options_db_path()))


def _sqlalchemy_sqlite_url(db_path: Path) -> str:
    """Absolute path URL so job store does not depend on process cwd."""
    return "sqlite:///" + str(db_path.resolve())


def _parse_wall_clock_et(value: object, default: str) -> tuple[int, int]:
    """Accept current HH:MM strings and legacy integer hour settings."""
    raw = default if value is None or value == "" else value
    try:
        if isinstance(raw, (int, float)):
            hour, minute = int(raw), 0
        else:
            text = str(raw).strip()
            if ":" in text:
                hour_s, minute_s = text.split(":", 1)
                hour, minute = int(hour_s), int(minute_s)
            else:
                hour, minute = int(text), 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (TypeError, ValueError):
        pass
    log.warning("scheduler: invalid ET time %r, falling back to %s", value, default)
    hour_s, minute_s = default.split(":", 1)
    return int(hour_s), int(minute_s)


def build_scheduler(repo: Repo) -> BackgroundScheduler:
    """Create and configure APScheduler with SQLAlchemyJobStore."""
    jobstores = {"default": SQLAlchemyJobStore(url=_sqlalchemy_sqlite_url(repo._path))}
    scheduler = BackgroundScheduler(
        jobstores=jobstores,
        timezone=_ET_TZ,
    )
    return scheduler


def register_jobs(scheduler: BackgroundScheduler, repo: Repo) -> None:
    """Register all recurring jobs from current settings."""
    settings = repo.get_settings()
    schedule = settings.get("schedule", {})
    screener_min = int(schedule.get("screener_minutes", 0))
    radar_min = int(schedule.get("radar_minutes", 15))
    settle_time = schedule.get("settlement_time_et", "16:30")
    iv_time = schedule.get("iv_refresh_time_et", "17:00")

    settle_h, settle_m = _parse_wall_clock_et(settle_time, "16:30")
    iv_h, iv_m = _parse_wall_clock_et(iv_time, "17:00")

    # Remove existing jobs to allow re-registration
    for jid in ["screener", "radar", "settlement", "iv_history", "option_pool_maintenance"]:
        try:
            scheduler.remove_job(jid)
        except Exception:
            pass

    if screener_min > 0:
        scheduler.add_job(
            job_screener_tick,
            IntervalTrigger(minutes=screener_min, timezone=_ET_TZ),
            id="screener",
            replace_existing=True,
            misfire_grace_time=300,
        )
    else:
        log.info("scheduler: screener job disabled (schedule.screener_minutes=%s)", screener_min)
    scheduler.add_job(
        job_radar_tick,
        IntervalTrigger(minutes=radar_min, timezone=_ET_TZ),
        id="radar",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        job_settlement_tick,
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
        job_iv_history_tick,
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
    scheduler.add_job(
        job_option_pool_maintenance_tick,
        CronTrigger(
            day_of_week=_TRADING_DAYS,
            hour=8,
            minute=0,
            timezone=_ET_TZ,
        ),
        id="option_pool_maintenance",
        replace_existing=True,
        misfire_grace_time=300,
    )
    screener_log = f"{screener_min}m" if screener_min > 0 else "off"
    log.info(
        "scheduler: jobs registered (screener=%s, radar=%dm, settle=%s, iv=%s)",
        screener_log, radar_min, settle_time, iv_time,
    )
