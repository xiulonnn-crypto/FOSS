from __future__ import annotations

from datetime import date
from typing import List, Optional, Tuple

from app.core.types import OptionContract, Quote


class MarketDataProvider:
    """Abstract base for all market data providers."""

    name: str = "base"
    realtime: bool = False

    def get_quote(self, symbol: str) -> Quote:
        raise NotImplementedError

    def get_expirations(self, symbol: str) -> List[date]:
        raise NotImplementedError

    def get_option_chain(
        self,
        symbol: str,
        expiration: date,
        right: str = "P",
    ) -> List[OptionContract]:
        raise NotImplementedError

    def get_historical_close(self, symbol: str, day: date) -> Optional[float]:
        raise NotImplementedError

    def get_iv_history(self, symbol: str, days: int = 252) -> List[Tuple[date, float]]:
        raise NotImplementedError

    def get_next_earnings(self, symbol: str) -> Optional[date]:
        return None
