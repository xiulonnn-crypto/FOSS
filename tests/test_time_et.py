from __future__ import annotations

import re
from datetime import date, datetime, timezone

import app.core.time_et as time_et_mod
from app.core.time_et import et_timestamp_for_filename, instant_to_et_iso


def test_instant_to_et_iso_winter_utc():
    dt = datetime(2026, 1, 15, 18, 30, 0, tzinfo=timezone.utc)
    s = instant_to_et_iso(dt)
    assert "2026-01-15T13:30:00" in s
    assert "-05:00" in s


def test_instant_to_et_iso_summer_utc():
    dt = datetime(2026, 5, 10, 16, 0, 0, tzinfo=timezone.utc)
    s = instant_to_et_iso(dt)
    assert "2026-05-10T12:00:00" in s
    assert "-04:00" in s


def test_et_timestamp_for_filename_pattern():
    assert re.fullmatch(r"\d{8}_\d{6}", et_timestamp_for_filename())


def test_et_calendar_today_follows_new_york_wall_date(monkeypatch):
    """Same instant can be calendar-next-day in Asia; DTE must use Eastern date."""

    class _FakeDateTime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 5, 13, 3, 59, 0, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(time_et_mod, "datetime", _FakeDateTime)
    assert time_et_mod.et_calendar_today() == date(2026, 5, 12)


def test_parse_instant_utc_accepts_t_and_space_format():
    from app.core.time_et import parse_instant_utc

    a = parse_instant_utc("2026-05-14T15:00:00+00:00")
    b = parse_instant_utc("2026-05-14 16:00:00+00:00")
    assert a is not None and b is not None
    assert a < b


def test_parse_instant_utc_z_suffix():
    from app.core.time_et import parse_instant_utc

    dt = parse_instant_utc("2026-05-14T15:00:00Z")
    assert dt is not None
    assert dt.hour == 15
