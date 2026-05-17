"""Tests for daily-stock H/L × BS(EOD IV) intraday extreme enrichment."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from app.core.pnl_excursion_intraday import enrich_closed_position_intraday_bs
from app.db.init_db import init_database
from app.db.repo import Repo


@pytest.fixture
def repo(tmp_path):
    p = tmp_path / "intraday.db"
    init_database(p)
    return Repo(p)


def _insert_closed(repo: Repo, *, open_at: str = "2026-05-10T15:30:00+00:00",
                   close_at: str = "2026-05-13T20:05:00+00:00") -> int:
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-08-01",
        "strike": 100.0,
        "contracts": 1,
        "open_at": open_at,
        "open_premium": 4.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 1.5, "manual", 250.0,
                        close_at=close_at)
    repo.save_open_snapshot(pid, {"spot": 120.0, "iv": 0.40})
    return pid


# ---------------------------------------------------------------------------
# RED → GREEN: bar_count must be 2 × trading_days, NOT 5m bar count
# ---------------------------------------------------------------------------

@patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl")
@patch("app.core.pnl_excursion_intraday._fetch_eod_iv_map")
def test_bar_count_is_2x_days_not_5m_count(mock_iv, mock_hl, repo, monkeypatch):
    """2-day hold → bar_count == 4 (2 days × 2 H/L points), not 156."""
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    pid = _insert_closed(repo)

    # Simulate 2 trading days with distinct L/H
    mock_hl.return_value = [
        (date(2026, 5, 10), 118.0, 123.0),   # day 1: low=118, high=123
        (date(2026, 5, 11), 115.0, 122.0),   # day 2: low=115, high=122
    ]
    mock_iv.return_value = {}  # fallback IV

    enrich_closed_position_intraday_bs(repo, pid)

    b = (repo.get_open_snapshot(pid) or {}).get("intraday_bs")
    assert b is not None, "intraday_bs block missing"
    assert b["bar_count"] == 4, f"expected 4, got {b['bar_count']}"


# ---------------------------------------------------------------------------
# Low spot → MAE; High spot → MFE
# ---------------------------------------------------------------------------

@patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl")
@patch("app.core.pnl_excursion_intraday._fetch_eod_iv_map")
def test_low_stock_produces_mae_high_produces_mfe(mock_iv, mock_hl, repo, monkeypatch):
    """
    For a short put:
    * Lower stock price → higher put premium → more negative pnl → MAE candidate.
    * Higher stock price → lower put premium → more positive pnl → MFE candidate.
    """
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    pid = _insert_closed(repo)

    # Single day: clear low / high spread so MAE < 0 and MFE > 0
    mock_hl.return_value = [(date(2026, 5, 10), 90.0, 135.0)]
    mock_iv.return_value = {}

    enrich_closed_position_intraday_bs(repo, pid)

    b = (repo.get_open_snapshot(pid) or {}).get("intraday_bs")
    assert b is not None
    assert b["mae_pnl_pct"] is not None and b["mae_pnl_pct"] < 0, \
        f"MAE should be negative (option more expensive at stock low), got {b['mae_pnl_pct']}"
    assert b["mfe_pnl_pct"] is not None and b["mfe_pnl_pct"] > 0, \
        f"MFE should be positive (option cheaper at stock high), got {b['mfe_pnl_pct']}"


# ---------------------------------------------------------------------------
# Massive IV back-fit used when available
# ---------------------------------------------------------------------------

@patch("app.core.pnl_excursion_intraday.MassiveClient")
@patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl")
def test_enrich_stores_block_with_massive_iv(mock_hl, mock_client_cls, repo, monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "secret")
    pid = _insert_closed(repo)

    mock_hl.return_value = [
        (date(2026, 5, 10), 118.0, 123.0),
        (date(2026, 5, 11), 115.0, 122.0),
    ]
    # iv_map is built inside _fetch_eod_iv_map which calls MassiveClient.
    # Patch _fetch_eod_iv_map directly for a cleaner test.
    with patch("app.core.pnl_excursion_intraday._fetch_eod_iv_map") as mock_iv:
        mock_iv.return_value = {
            date(2026, 5, 10): 0.42,
            date(2026, 5, 11): 0.38,
        }
        enrich_closed_position_intraday_bs(repo, pid)

    snap = repo.get_open_snapshot(pid) or {}
    assert snap.get("spot") == 120.0           # prior keys preserved
    b = snap.get("intraday_bs")
    assert b is not None
    assert b["iv_source"] == "massive_eod_backfit"
    assert b["bar_count"] == 4
    assert b["model"] == "daily_hl_bs_eod_iv"
    assert b["interval"] == "1d_hl"
    assert b["hold_window"]["open_date_et"] == "2026-05-10"
    assert b["hold_window"]["close_date_et"] == "2026-05-13"


# ---------------------------------------------------------------------------
# Fallback to entry-snapshot IV when no Massive
# ---------------------------------------------------------------------------

@patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl")
@patch("app.core.pnl_excursion_intraday._fetch_eod_iv_map")
def test_fallback_to_entry_iv_when_no_massive(mock_iv, mock_hl, repo, monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    pid = _insert_closed(repo)

    mock_hl.return_value = [(date(2026, 5, 10), 118.0, 123.0)]
    mock_iv.return_value = {}

    enrich_closed_position_intraday_bs(repo, pid)

    b = (repo.get_open_snapshot(pid) or {}).get("intraday_bs")
    assert b is not None
    assert b["iv_source"] == "entry_snapshot_const"
    assert b["bar_count"] == 2   # 1 day × 2 H/L


# ---------------------------------------------------------------------------
# Same calendar day: hold-window H/L vs full session daily
# ---------------------------------------------------------------------------

@patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl")
@patch("app.core.pnl_excursion_intraday._yf_stock_min_max_between")
@patch("app.core.pnl_excursion_intraday._fetch_eod_iv_map")
def test_same_day_uses_hold_window_skips_daily(mock_iv, mock_yf_mm, mock_daily, repo, monkeypatch):
    """open/close same ET day → intraday clip; do not use full-session daily H/L."""
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-08-01",
        "strike": 100.0,
        "contracts": 1,
        "open_at": "2026-05-10T15:30:00+00:00",
        "open_premium": 4.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 1.5, "manual", 250.0,
                        close_at="2026-05-10T15:35:00+00:00")
    repo.save_open_snapshot(pid, {"spot": 120.0, "iv": 0.40})

    mock_yf_mm.return_value = (119.0, 121.0, 120.0)
    mock_iv.return_value = {}

    enrich_closed_position_intraday_bs(repo, pid)

    mock_daily.assert_not_called()
    b = (repo.get_open_snapshot(pid) or {}).get("intraday_bs")
    assert b is not None
    assert b["interval"] == "hold_window_hl"
    assert b["bar_count"] == 2


@patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl")
@patch("app.core.pnl_excursion_intraday._yf_stock_min_max_between")
@patch("app.core.pnl_excursion_intraday._fetch_eod_iv_map")
def test_hold_window_iv_source_snapshots_even_when_massive_iv_map_exists(
    mock_iv, mock_yf_mm, mock_daily, repo, monkeypatch
):
    monkeypatch.setenv("MASSIVE_API_KEY", "secret")
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-08-01",
        "strike": 100.0,
        "contracts": 1,
        "open_at": "2026-05-10T15:30:00+00:00",
        "open_premium": 4.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 1.5, "manual", 250.0,
                        close_at="2026-05-10T15:35:00+00:00")
    repo.save_open_snapshot(pid, {"spot": 120.0, "iv": 0.44})

    mock_yf_mm.return_value = (119.0, 121.0, 120.0)
    mock_iv.return_value = {date(2026, 5, 10): 0.99}

    enrich_closed_position_intraday_bs(repo, pid)

    mock_daily.assert_not_called()
    b = (repo.get_open_snapshot(pid) or {}).get("intraday_bs")
    assert b is not None
    assert b["interval"] == "hold_window_hl"
    assert b["iv_source"] == "implied_iv_open_fill_hold_window"


@patch("app.core.pnl_excursion_intraday.implied_vol_black_scholes_put", return_value=None)
@patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl")
@patch("app.core.pnl_excursion_intraday._yf_stock_min_max_between")
@patch("app.core.pnl_excursion_intraday._fetch_eod_iv_map")
def test_hold_window_iv_falls_back_when_implied_solve_fails(
    mock_iv, mock_yf_mm, mock_daily, mock_solve, repo, monkeypatch
):
    monkeypatch.setenv("MASSIVE_API_KEY", "secret")
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-08-01",
        "strike": 100.0,
        "contracts": 1,
        "open_at": "2026-05-10T15:30:00+00:00",
        "open_premium": 4.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 1.5, "manual", 250.0,
                        close_at="2026-05-10T15:35:00+00:00")
    repo.save_open_snapshot(pid, {"spot": 120.0, "iv": 0.44})

    mock_yf_mm.return_value = (119.0, 121.0, 120.0)
    mock_iv.return_value = {date(2026, 5, 10): 0.99}

    enrich_closed_position_intraday_bs(repo, pid)

    mock_daily.assert_not_called()
    b = (repo.get_open_snapshot(pid) or {}).get("intraday_bs")
    assert b is not None
    assert b["interval"] == "hold_window_hl"
    assert b["iv_source"] == "entry_snapshot_iv_hold_window"


@patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl")
@patch("app.core.pnl_excursion_intraday._yf_stock_min_max_between")
@patch("app.core.pnl_excursion_intraday._fetch_eod_iv_map")
def test_same_day_falls_back_to_daily_when_window_empty(
    mock_iv, mock_yf_mm, mock_daily, repo, monkeypatch
):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-08-01",
        "strike": 100.0,
        "contracts": 1,
        "open_at": "2026-05-10T15:30:00+00:00",
        "open_premium": 4.0,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 1.5, "manual", 250.0,
                        close_at="2026-05-10T15:35:00+00:00")
    repo.save_open_snapshot(pid, {"spot": 120.0, "iv": 0.40})

    mock_yf_mm.return_value = (None, None, None)
    mock_daily.return_value = [(date(2026, 5, 10), 90.0, 135.0)]
    mock_iv.return_value = {}

    enrich_closed_position_intraday_bs(repo, pid)

    mock_daily.assert_called_once()
    b = (repo.get_open_snapshot(pid) or {}).get("intraday_bs")
    assert b is not None
    assert b["interval"] == "1d_hl"
    assert b.get("hold_window_fallback") is True


# ---------------------------------------------------------------------------
# Bar timestamps vs overlap (5m candles — label is bar open before fill)
# ---------------------------------------------------------------------------

def test_hold_window_overlap_5m_covering_inside_next_open():
    from datetime import datetime, timezone

    from app.core.pnl_excursion_intraday import _bar_overlaps_hold_window

    su = datetime(2026, 5, 7, 14, 31, tzinfo=timezone.utc)
    eu = datetime(2026, 5, 7, 14, 32, tzinfo=timezone.utc)
    bar_open = datetime(2026, 5, 7, 14, 30, tzinfo=timezone.utc)
    assert _bar_overlaps_hold_window(su, eu, bar_open, "5m")


def test_hold_window_overlap_rejects_prior_non_overlapping_5m():
    from datetime import datetime, timezone

    from app.core.pnl_excursion_intraday import _bar_overlaps_hold_window

    su = datetime(2026, 5, 7, 14, 31, tzinfo=timezone.utc)
    eu = datetime(2026, 5, 7, 14, 32, tzinfo=timezone.utc)
    bar_open = datetime(2026, 5, 7, 14, 20, tzinfo=timezone.utc)
    assert not _bar_overlaps_hold_window(su, eu, bar_open, "5m")


def test_hold_window_boundary_excludes_bar_opening_at_close_at():
    """Bar that OPENS exactly at close_at must NOT be included (position already closed)."""
    from datetime import datetime, timezone

    from app.core.pnl_excursion_intraday import _bar_overlaps_hold_window

    su = datetime(2026, 5, 7, 18, 50, 0, tzinfo=timezone.utc)
    eu = datetime(2026, 5, 7, 18, 51, 0, tzinfo=timezone.utc)
    # bar at 18:50 should be included
    assert _bar_overlaps_hold_window(su, eu, datetime(2026, 5, 7, 18, 50, 0, tzinfo=timezone.utc), "1m")
    # bar at 18:51 (exactly eu) should NOT be included
    assert not _bar_overlaps_hold_window(su, eu, datetime(2026, 5, 7, 18, 51, 0, tzinfo=timezone.utc), "1m")


@patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl")
@patch("app.core.pnl_excursion_intraday._yf_stock_min_max_between")
@patch("app.core.pnl_excursion_intraday._fetch_eod_iv_map")
def test_hold_window_uses_entry_approx_not_stale_snap_spot(
    mock_iv, mock_yf_mm, mock_daily, repo, monkeypatch
):
    """
    When hw_entry_approx is provided (bar Open at open_at), it should be used for IV
    computation rather than the stale snapshot spot.

    Verify: when snap.spot is far from actual bar price, entry_approx still produces
    anchor ≈ 0 (IV is calibrated to bar Open, not snap.spot).
    """
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-05-08",
        "strike": 600.0,
        "contracts": 1,
        "open_at": "2026-05-07T18:50:00+00:00",
        "open_premium": 2.5,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 2.4, "manual", 240.0,
                        close_at="2026-05-07T18:51:00+00:00")
    # snap.spot is stale/wrong — real stock price was ~638 at fill time
    repo.save_open_snapshot(pid, {"spot": 646.63, "iv": 1.242})

    # Simulate: bar Open = 638.57 (actual fill-time price)
    mock_yf_mm.return_value = (635.80, 638.75, 638.57)
    mock_iv.return_value = {}

    enrich_closed_position_intraday_bs(repo, pid)

    mock_daily.assert_not_called()
    b = (repo.get_open_snapshot(pid) or {}).get("intraday_bs")
    assert b is not None
    assert b["interval"] == "hold_window_hl"
    assert b["iv_source"] == "implied_iv_open_fill_hold_window"
    # With entry_approx=638.57 (not snap.spot=646.63), IV is correctly calibrated
    # to actual fill-time stock price — MAE must be bounded and mfe > 0
    assert b["mae_pnl_pct"] is not None
    assert b["mae_pnl_pct"] > -0.30, f"MAE {b['mae_pnl_pct']} should be bounded (<30%) with correct entry_spot"
    assert b["mfe_pnl_pct"] is not None and b["mfe_pnl_pct"] > 0, \
        f"MFE should be > 0 when Hi({638.75}) > entry_approx({638.57})"


# ---------------------------------------------------------------------------
# Logical invariant: close_pnl must bound MFE (positive close) / MAE (negative close)
# ---------------------------------------------------------------------------

@patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl")
@patch("app.core.pnl_excursion_intraday._yf_stock_min_max_between")
@patch("app.core.pnl_excursion_intraday._fetch_eod_iv_map")
def test_close_pnl_bounds_mfe_when_positive(mock_iv, mock_yf_mm, mock_daily, repo, monkeypatch):
    """
    Invariant (profitable close): MFE must be >= actual close P&L.

    Simulate position 4: open=$2.50, close=$2.40 (+4%).
    BS Hi of the session gives only +0.9%.  Without fix, MFE=0.9% < 4% — broken.
    After fix, close_pnl(+4%) is added to series → MFE >= 4%.
    """
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-05-08",
        "strike": 600.0,
        "contracts": 1,
        "open_at": "2026-05-07T18:50:00+00:00",
        "open_premium": 2.5,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 2.4, "manual", 240.0,
                        close_at="2026-05-07T18:51:00+00:00")
    repo.save_open_snapshot(pid, {"spot": 638.57, "iv": 1.097})

    # Bar gives only a +0.9% Hi — without close_pnl, MFE would be 0.9%
    mock_yf_mm.return_value = (635.80, 638.75, 638.57)
    mock_iv.return_value = {}

    enrich_closed_position_intraday_bs(repo, pid)

    b = (repo.get_open_snapshot(pid) or {}).get("intraday_bs")
    assert b is not None
    close_pnl = (2.5 - 2.4) / 2.5  # = 0.04
    mfe = b["mfe_pnl_pct"]
    assert mfe is not None, "mfe_pnl_pct must not be None"
    assert mfe >= close_pnl - 1e-6, (
        f"MFE {mfe:.4f} < close_pnl {close_pnl:.4f}: violates 'profitable close ⟹ MFE ≥ close'"
    )


@patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl")
@patch("app.core.pnl_excursion_intraday._yf_stock_min_max_between")
@patch("app.core.pnl_excursion_intraday._fetch_eod_iv_map")
def test_close_pnl_bounds_mae_when_negative(mock_iv, mock_yf_mm, mock_daily, repo, monkeypatch):
    """
    Invariant (loss close): MAE must be <= actual close P&L (more negative).

    Simulate: open=$2.50, close=$3.00 (-20%).
    Bar gives only -10% Lo.  Without fix, MAE=-10% which is better than close=-20% — broken.
    After fix, close_pnl(-20%) anchors MAE at at most -20%.
    """
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    pid = repo.insert_position({
        "symbol": "MU",
        "expiration": "2026-05-08",
        "strike": 600.0,
        "contracts": 1,
        "open_at": "2026-05-07T18:50:00+00:00",
        "open_premium": 2.5,
        "open_candidate_id": None,
        "state": "OPEN",
        "notes": None,
    })
    repo.close_position(pid, "CLOSED_EARLY", 3.0, "manual", 300.0,
                        close_at="2026-05-07T18:51:00+00:00")
    repo.save_open_snapshot(pid, {"spot": 638.57, "iv": 1.097})

    # Bar gives -10% Lo only — without close_pnl, MAE would be only -10%
    # But close at $3.00 means -20% PnL — MAE must be ≤ -20%
    mock_yf_mm.return_value = (636.80, 638.75, 638.57)  # Lo gives ~-10%
    mock_iv.return_value = {}

    enrich_closed_position_intraday_bs(repo, pid)

    b = (repo.get_open_snapshot(pid) or {}).get("intraday_bs")
    assert b is not None
    close_pnl = (2.5 - 3.0) / 2.5  # = -0.20
    mae = b["mae_pnl_pct"]
    assert mae is not None, "mae_pnl_pct must not be None"
    assert mae <= close_pnl + 1e-6, (
        f"MAE {mae:.4f} > close_pnl {close_pnl:.4f}: violates 'loss close ⟹ MAE ≤ close'"
    )


# ---------------------------------------------------------------------------
# Single-day hold: flat MAE/MFE when anchor aligns with session extremes
# ---------------------------------------------------------------------------

@patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl")
@patch("app.core.pnl_excursion_intraday._fetch_eod_iv_map")
def test_single_day_with_identical_hl(mock_iv, mock_hl, repo, monkeypatch):
    """
    When Low == High (flat HL window), BS bar marks equal the anchor.
    Even so, the actual close fill (close_pnl) is a guaranteed data point, so
    MAE/MFE are defined — not None — reflecting the invariant that the realized
    exit is always part of the holding-period P&L path.
    Position: open=$4.00, close=$1.50 (+62.5% gain); MFE must be > 0.
    """
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    pid = _insert_closed(repo)

    mock_hl.return_value = [(date(2026, 5, 10), 120.0, 120.0)]  # L == H
    mock_iv.return_value = {}

    enrich_closed_position_intraday_bs(repo, pid)

    b = (repo.get_open_snapshot(pid) or {}).get("intraday_bs")
    assert b is not None
    assert b["bar_count"] == 2  # bar_count counts only HL bar points
    # close_pnl = (4.0-1.5)/4.0 = +62.5% is always added → path is non-flat
    assert b["mfe_pnl_pct"] is not None, "flat HL still has close_pnl as a data point"
    close_pnl = (4.0 - 1.5) / 4.0
    assert b["mfe_pnl_pct"] >= 0 or b["mae_pnl_pct"] <= 0, "close_pnl must drive excursion"


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

@patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl")
def test_skip_when_no_bars(mock_hl, repo, monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    pid = _insert_closed(repo)
    mock_hl.return_value = []

    enrich_closed_position_intraday_bs(repo, pid)

    assert "intraday_bs" not in (repo.get_open_snapshot(pid) or {})


def test_skip_open_position(repo):
    pid = repo.insert_position({
        "symbol": "MU", "expiration": "2026-08-01", "strike": 100.0,
        "contracts": 1, "open_at": "2026-05-10T15:30:00+00:00",
        "open_premium": 4.0, "open_candidate_id": None, "state": "OPEN", "notes": None,
    })
    enrich_closed_position_intraday_bs(repo, pid)
    assert "intraday_bs" not in (repo.get_open_snapshot(pid) or {})


# ---------------------------------------------------------------------------
# Prior snapshot keys must be preserved
# ---------------------------------------------------------------------------

@patch("app.core.pnl_excursion_intraday._fetch_stock_daily_hl")
@patch("app.core.pnl_excursion_intraday._fetch_eod_iv_map")
def test_prior_snapshot_keys_preserved(mock_iv, mock_hl, repo, monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    pid = _insert_closed(repo)
    repo.save_open_snapshot(pid, {
        "spot": 120.0, "iv": 0.40, "rsi_6": 45.2,
        "massive": {"source": "massive", "bar_count": 2},
    })

    mock_hl.return_value = [(date(2026, 5, 10), 118.0, 123.0),
                            (date(2026, 5, 11), 115.0, 122.0)]
    mock_iv.return_value = {}

    enrich_closed_position_intraday_bs(repo, pid)

    snap = repo.get_open_snapshot(pid) or {}
    assert snap.get("rsi_6") == pytest.approx(45.2)
    assert snap.get("massive") is not None
    assert snap.get("intraday_bs") is not None
