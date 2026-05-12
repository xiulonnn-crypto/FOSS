from __future__ import annotations

from app.data.provider_base import MarketDataProvider


class TradierProvider(MarketDataProvider):
    """Tradier API provider — not yet implemented."""

    name = "tradier"
    realtime = True

    def __init__(self, api_key: str, base_url: str = "https://sandbox.tradier.com/v1"):
        self.api_key = api_key
        self.base_url = base_url

    def get_quote(self, symbol: str):
        raise NotImplementedError("TradierProvider not yet implemented")

    def get_expirations(self, symbol: str):
        raise NotImplementedError

    def get_option_chain(self, symbol, expiration, right="P"):
        raise NotImplementedError

    def get_historical_close(self, symbol, day):
        raise NotImplementedError

    def get_iv_history(self, symbol, days=252):
        raise NotImplementedError
