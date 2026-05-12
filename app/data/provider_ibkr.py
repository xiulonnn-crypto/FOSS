from __future__ import annotations

from app.data.provider_base import MarketDataProvider


class IBKRProvider(MarketDataProvider):
    """IBKR (ib_insync) provider — placeholder for v2."""

    name = "ibkr"
    realtime = True

    def get_quote(self, symbol: str):
        raise NotImplementedError("IBKRProvider not yet implemented (v2)")

    def get_expirations(self, symbol: str):
        raise NotImplementedError

    def get_option_chain(self, symbol, expiration, right="P"):
        raise NotImplementedError

    def get_historical_close(self, symbol, day):
        raise NotImplementedError

    def get_iv_history(self, symbol, days=252):
        raise NotImplementedError
