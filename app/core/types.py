from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    expiration: date
    strike: float
    right: str  # "P" or "C"
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    iv: Optional[float]
    delta: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    gamma: Optional[float]
    open_interest: Optional[int]
    volume: Optional[int]
    quote_age_seconds: Optional[int] = None

    @property
    def mid(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2.0
        return None


@dataclass(frozen=True)
class Quote:
    symbol: str
    spot: float
    asof: datetime
    iv_rank: Optional[float] = None  # 0-100, RV proxy


@dataclass
class Settings:
    filters: dict = field(default_factory=dict)
    exits: dict = field(default_factory=dict)
    scoring_weights: dict = field(default_factory=dict)
    schedule: dict = field(default_factory=dict)
    provider: str = "yfinance"
    fees: dict = field(default_factory=lambda: {"usd_per_contract": 1.0})
    risk_free_rate: float = 0.045
    rv_by_symbol: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        return cls(
            filters=d.get("filters", {}),
            exits=d.get("exits", {}),
            scoring_weights=d.get("scoring_weights", {}),
            schedule=d.get("schedule", {}),
            provider=d.get("provider", "yfinance"),
            fees=d.get("fees", {"usd_per_contract": 1.0}),
            risk_free_rate=d.get("risk_free_rate", 0.045),
            rv_by_symbol=d.get("rv_by_symbol", {}),
        )
