"""Massive REST client rate limit and URL building."""

from datetime import date
from unittest.mock import MagicMock, patch

from app.data.massive_client import MassiveClient, MassiveRateLimiter


def test_rate_limiter_allows_burst_under_cap():
    lim = MassiveRateLimiter(max_calls=3, period_sec=60.0)
    for _ in range(3):
        lim.acquire()


def test_massive_client_follows_next_url(monkeypatch):
    session = MagicMock()
    first = MagicMock()
    first.status_code = 200
    first.json.return_value = {
        "status": "OK",
        "results": [{"t": 1, "c": 1.0}],
        "next_url": "https://api.massive.com/v2/aggs/ticker/O%3AX/range/1/day/2020-01-01/2020-01-02?cursor=abc",
    }
    second = MagicMock()
    second.status_code = 200
    second.json.return_value = {"status": "OK", "results": [{"t": 2, "c": 2.0}]}
    session.get.side_effect = [first, second]

    c = MassiveClient("test-key", session=session, limiter=MassiveRateLimiter(10, 60.0))
    rows = c.fetch_daily_aggs("O:TEST", date(2020, 1, 1), date(2020, 1, 2))

    assert len(rows) == 2
    assert session.get.call_count == 2
    second_url = session.get.call_args_list[1][0][0]
    assert "apiKey=test-key" in second_url
