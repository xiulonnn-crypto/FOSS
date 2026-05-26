from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from app.core.types import OptionContract
from app.data.provider_base import MarketDataProvider
from app.db.repo import Repo

log = logging.getLogger(__name__)


def run_iv_snapshot(
    repo: Repo,
    provider: MarketDataProvider,
    *,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Persist one local IV30 / skew / VIX snapshot per enabled symbol."""
    today_d = today or date.today()
    watchlist = repo.list_enabled_watchlist_symbols()
    settings = repo.get_settings()
    iv_by_symbol = settings.get("iv_by_symbol") or {}
    updated = 0
    errors: List[Dict[str, str]] = []

    vix = _fetch_vix(provider)
    for symbol in watchlist:
        try:
            quote = provider.get_quote(symbol)
            expirations = provider.get_expirations(symbol)
            expiration = _pick_expiration(expirations, today_d)
            if expiration is None:
                continue
            puts = provider.get_option_chain(
                symbol,
                expiration,
                right="P",
                underlying_spot=quote.spot,
            )
            calls = provider.get_option_chain(
                symbol,
                expiration,
                right="C",
                underlying_spot=quote.spot,
            )
            atm_put = _pick_atm_contract(puts, quote.spot)
            if atm_put is None or atm_put.iv is None:
                continue
            put_skew = _pick_otm_delta_contract(puts, quote.spot, right="P")
            call_skew = _pick_otm_delta_contract(calls, quote.spot, right="C")
            skew = None
            if put_skew and call_skew and put_skew.iv is not None and call_skew.iv is not None:
                skew = round(float(put_skew.iv) - float(call_skew.iv), 6)

            iv30 = round(float(atm_put.iv), 6)
            repo.upsert_market_iv_snapshot(
                {
                    "symbol": symbol,
                    "as_of_date": today_d,
                    "iv30": iv30,
                    "atm_strike": atm_put.strike,
                    "skew": skew,
                    "vix": vix,
                    "source": provider.name,
                }
            )
            history = list(iv_by_symbol.get(symbol) or [])
            history.append(iv30)
            iv_by_symbol[symbol] = history[-252:]
            updated += 1
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
            log.warning("iv_snapshot: %s error: %s", symbol, exc)

    if updated:
        repo.merge_settings({"iv_by_symbol": iv_by_symbol})
    log.info("iv_snapshot: updated=%d errors=%d", updated, len(errors))
    return {"updated": updated, "errors": errors}


def _fetch_vix(provider: MarketDataProvider) -> Optional[float]:
    try:
        return float(provider.get_quote("^VIX").spot)
    except Exception as exc:
        log.debug("iv_snapshot: VIX unavailable: %s", exc)
        return None


def _pick_expiration(expirations: List[date], today: date) -> Optional[date]:
    valid = [exp for exp in expirations if exp > today]
    if not valid:
        return None
    return min(valid, key=lambda exp: abs((exp - today).days - 30))


def _pick_atm_contract(contracts: List[OptionContract], spot: float) -> Optional[OptionContract]:
    valid = [c for c in contracts if c.iv is not None and c.strike is not None]
    if not valid:
        return None
    return min(valid, key=lambda c: abs(float(c.strike) - float(spot)))


def _pick_otm_delta_contract(
    contracts: List[OptionContract],
    spot: float,
    *,
    right: str,
) -> Optional[OptionContract]:
    expected = right.upper()
    valid = [
        c for c in contracts
        if c.iv is not None and str(c.right).upper() == expected and _is_otm(c, spot, expected)
    ]
    if not valid:
        return None
    with_delta = [c for c in valid if c.delta is not None]
    if with_delta:
        return min(with_delta, key=lambda c: abs(abs(float(c.delta or 0.0)) - 0.25))
    return min(valid, key=lambda c: abs(abs(float(c.strike) / float(spot) - 1.0) - 0.10))


def _is_otm(contract: OptionContract, spot: float, right: str) -> bool:
    if right == "P":
        return float(contract.strike) < float(spot)
    return float(contract.strike) > float(spot)
