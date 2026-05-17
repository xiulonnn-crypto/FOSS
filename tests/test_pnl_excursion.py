import pytest

from app.core.pnl_excursion import relative_mae_mfe_from_pnls_chronologic


def test_relative_excursion_two_point_path():
    baseline_first = [-0.15, 0.35]
    mae, mfe = relative_mae_mfe_from_pnls_chronologic(baseline_first)
    assert mae == 0.0
    assert mfe == pytest.approx(0.5)


def test_relative_excursion_flat_returns_none():
    assert relative_mae_mfe_from_pnls_chronologic([0.0, 0.0]) == (None, None)


def test_relative_excursion_short_series():
    assert relative_mae_mfe_from_pnls_chronologic([0.1]) == (None, None)
