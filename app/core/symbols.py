"""Ticker string normalization for user-facing inputs (watchlist, forms)."""

from __future__ import annotations

import re
import unicodedata

_INVISIBLE_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u2060]")


def normalize_ticker_symbol(s: str) -> str:
    """Map fullwidth / compatibility Latin tickers to ASCII (e.g. ＭＵ → MU)."""
    t = unicodedata.normalize("NFKC", s or "")
    t = _INVISIBLE_RE.sub("", t)
    return t.strip().upper()
