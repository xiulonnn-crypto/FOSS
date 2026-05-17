from __future__ import annotations

import pytest
from app.core.settlement import settle_short_put, calc_realized_pnl


# ------------------------------------------------------------------
# settle_short_put
# ------------------------------------------------------------------

def test_spot_above_strike_is_otm():
    assert settle_short_put(spot_close=152.0, strike=150.0) == "expired_otm"


def test_spot_equal_strike_is_assigned():
    # spot == strike → Put is exactly at money → ITM (seller assigned)
    assert settle_short_put(spot_close=150.0, strike=150.0) == "assigned"


def test_spot_below_strike_is_assigned():
    assert settle_short_put(spot_close=140.0, strike=150.0) == "assigned"


def test_deep_otm():
    assert settle_short_put(spot_close=200.0, strike=150.0) == "expired_otm"


def test_deep_itm():
    assert settle_short_put(spot_close=100.0, strike=150.0) == "assigned"


# ------------------------------------------------------------------
# calc_realized_pnl
# ------------------------------------------------------------------

def test_full_credit_at_expiry():
    # Opened for 2.00 premium, expired OTM (close_premium=0), 1 contract, $1 fee
    pnl = calc_realized_pnl(open_premium=2.0, close_premium=0.0, contracts=1, fee_per_contract=1.0)
    assert pnl == 2.0 * 100 - 1.0  # 199.0


def test_early_close_50_pct():
    # Opened for 2.00, closed at 1.00 (50% profit), 2 contracts; open + close commissions
    pnl = calc_realized_pnl(
        open_premium=2.0,
        close_premium=1.0,
        contracts=2,
        fee_per_contract=1.0,
        fee_legs=2,
    )
    assert pnl == (2.0 - 1.0) * 100 * 2 - 4.0  # 196.0


def test_loss_on_assigned():
    # Opened for 2.00, assigned — close at intrinsic 5.00; open + close (assignment) commissions
    pnl = calc_realized_pnl(
        open_premium=2.0,
        close_premium=5.0,
        contracts=1,
        fee_per_contract=1.0,
        fee_legs=2,
    )
    assert pnl == (2.0 - 5.0) * 100 - 2.0  # -302.0
