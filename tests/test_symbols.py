from app.core.symbols import normalize_ticker_symbol


def test_normalize_ticker_fullwidth_latin_to_ascii():
    assert normalize_ticker_symbol("ＭＵ") == "MU"
    assert normalize_ticker_symbol("ｍｕ") == "MU"


def test_normalize_ticker_strips_zwsp():
    assert normalize_ticker_symbol("M\u200bU") == "MU"


def test_normalize_ticker_ascii_unchanged():
    assert normalize_ticker_symbol("mu") == "MU"
    assert normalize_ticker_symbol("  QQQ  ") == "QQQ"
