"""US Eastern (America/New_York) helpers for display filenames and API fields."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

APP_TZ = ZoneInfo("America/New_York")


def parse_instant_utc(raw: object) -> Optional[datetime]:
    """
    Parse API / SQLite instant strings to aware UTC datetime.
    Accepts ISO with 'T' or space, trailing Z, and naive values (treated as UTC).
    Returns None if missing or unparseable (never use lexicographic compare on raw strings).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def et_calendar_today() -> date:
    """US Eastern calendar date (e.g. option DTE convention), not server local date."""
    return datetime.now(APP_TZ).date()


def instant_to_et_iso(dt: datetime) -> str:
    """Convert an instant to ISO-8601 text with America/New_York offset."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(APP_TZ).isoformat()


def et_timestamp_for_filename() -> str:
    """Compact timestamp for snapshot filenames (wall clock in US Eastern)."""
    return datetime.now(APP_TZ).strftime("%Y%m%d_%H%M%S")
