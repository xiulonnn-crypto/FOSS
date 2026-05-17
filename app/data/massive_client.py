"""Massive REST client: daily aggregates with a process-local 5 calls/minute limiter."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import date, datetime, timezone
from typing import Any, Deque, Dict, List, Optional
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

import requests

_LOG = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.massive.com"
_MAX_CALLS_PER_MINUTE = 5
_PERIOD_SEC = 60.0


class MassiveRateLimiter:
    """Thread-safe limiter: at most ``max_calls`` per ``period_sec`` (monotonic clock)."""

    def __init__(self, max_calls: int = _MAX_CALLS_PER_MINUTE, period_sec: float = _PERIOD_SEC) -> None:
        self._max = max_calls
        self._period = period_sec
        self._lock = threading.Lock()
        self._times: Deque[float] = deque()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._times and now - self._times[0] >= self._period:
                    self._times.popleft()
                if len(self._times) < self._max:
                    self._times.append(now)
                    return
                wait = self._period - (now - self._times[0]) + 0.05
            time.sleep(max(wait, 0.05))


_global_limiter = MassiveRateLimiter()


def _ensure_api_key(url: str, api_key: str) -> str:
    parsed = urlparse(url)
    q = parse_qs(parsed.query, keep_blank_values=True)
    if "apiKey" not in q or not (q.get("apiKey") or [""])[0]:
        q["apiKey"] = [api_key]
    new_query = urlencode({k: v[0] for k, v in q.items()}, doseq=False)
    return urlunparse(parsed._replace(query=new_query))


class MassiveClient:
    """Minimal v2 aggregates client (EOD-friendly daily bars)."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        session: Optional[requests.Session] = None,
        limiter: Optional[MassiveRateLimiter] = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._base = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._session = session or requests.Session()
        self._limiter = limiter or _global_limiter

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def fetch_daily_aggs(
        self,
        options_ticker: str,
        start: date,
        end: date,
        *,
        timeout_sec: float = 45.0,
        max_pages: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Return Massive aggregate rows (``t`` in ms, ``o,h,l,c,v``) from ``start`` to ``end`` inclusive.
        Follows ``next_url`` up to ``max_pages``. Empty list if none or error (logged).
        """
        if not self._api_key:
            return []
        if end < start:
            return []

        start_s = start.isoformat()
        end_s = end.isoformat()
        path = f"/v2/aggs/ticker/{quote(options_ticker, safe='')}/range/1/day/{start_s}/{end_s}"
        url = f"{self._base}{path}?adjusted=true&sort=asc&limit=50000"
        url = _ensure_api_key(url, self._api_key)

        out: List[Dict[str, Any]] = []
        pages = 0
        next_url: Optional[str] = url
        while next_url and pages < max_pages:
            self._limiter.acquire()
            try:
                resp = self._session.get(next_url, timeout=timeout_sec)
            except requests.RequestException as exc:
                _LOG.info("massive request failed: %s", exc)
                return out
            if resp.status_code != 200:
                _LOG.info(
                    "massive HTTP %s for %s",
                    resp.status_code,
                    next_url.split("?", 1)[0],
                )
                return out
            try:
                payload = resp.json()
            except ValueError:
                _LOG.info("massive invalid JSON")
                return out
            status = str(payload.get("status") or "")
            if status and status.upper() not in ("OK", "DELAYED"):
                _LOG.info("massive status=%s results=%s", status, payload.get("resultsCount"))
            batch = payload.get("results") or []
            if isinstance(batch, list):
                out.extend(batch)
            nu = payload.get("next_url")
            if isinstance(nu, str) and nu:
                next_url = _ensure_api_key(nu, self._api_key)
            else:
                next_url = None
            pages += 1
        return out


def aggs_bar_date_et_ms(ms: int) -> date:
    """Calendar date in America/New_York for aggregate bar open ``t`` (ms UTC)."""
    from app.core.time_et import APP_TZ

    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).astimezone(APP_TZ)
    return dt.date()
