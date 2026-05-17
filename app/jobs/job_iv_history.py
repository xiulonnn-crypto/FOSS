from __future__ import annotations

import json
import logging

from app.data.provider_base import MarketDataProvider
from app.db.repo import Repo

log = logging.getLogger(__name__)


def run_iv_history(
    repo: Repo,
    provider: MarketDataProvider,
) -> None:
    """
    Refresh RV (realized volatility) history for all enabled watchlist symbols.
    Stores annualised RV values (last 252 data points) into settings.rv_by_symbol.
    """
    settings = repo.get_settings()
    watchlist = repo.list_enabled_watchlist_symbols()

    rv_by_symbol = settings.get("rv_by_symbol", {})
    updated = 0

    for symbol in watchlist:
        try:
            history = provider.get_iv_history(symbol, days=252)
            if not history:
                log.debug("iv_history: no data for %s", symbol)
                continue
            rv_values = [float(rv) for _, rv in history]
            rv_by_symbol[symbol] = rv_values
            updated += 1
        except Exception as exc:
            log.warning("iv_history: %s error: %s", symbol, exc)

    if updated > 0:
        repo.merge_settings({"rv_by_symbol": rv_by_symbol})
        log.info("iv_history: updated %d symbols", updated)
    else:
        log.info("iv_history: nothing updated")
