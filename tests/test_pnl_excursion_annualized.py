import pytest

from app.core.pnl_excursion import compute_annualized_return


def test_compute_annualized_return_one_year_hold():
    ann = compute_annualized_return(0.10, 365.0)
    assert ann == pytest.approx(0.10, rel=1e-3)


def test_compute_annualized_return_short_hold():
    ann = compute_annualized_return(0.05, 30.0)
    assert ann is not None
    assert ann > 0.05
