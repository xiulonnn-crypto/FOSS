from app.core.close_reason_norm import canonical_close_reason_code, close_reason_bucket


def test_canonical_take_profit():
    assert canonical_close_reason_code("take_profit_50") == "take_profit"
    assert canonical_close_reason_code("take_profit_fast") == "take_profit_fast"


def test_canonical_assigned():
    assert canonical_close_reason_code("assigned") == "assigned"


def test_close_reason_bucket_label():
    key, label, _order = close_reason_bucket("take_profit_75")
    assert key == "take_profit"
    assert "止盈" in label
