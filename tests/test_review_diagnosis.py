"""Tests for per-position review diagnosis API."""

from __future__ import annotations

import pytest

from app.core.review_highlights import build_position_highlights
from app.core.review_analytics import build_position_dimension_summary


def test_dimension_summary_has_nine_dimensions():
    pos = {
        "close_reason": "take_profit_50",
        "open_at": "2026-01-01T15:00:00+00:00",
        "close_at": "2026-01-15T20:00:00+00:00",
    }
    snap = {
        "quality_grade": "A",
        "entry_signal_status": "OPENABLE",
        "delta": -0.12,
        "dte": 35,
        "iv_rank": 55,
        "margin_buffer": 0.18,
        "rsi_12": 45,
    }
    dims = build_position_dimension_summary(pos, snap)
    assert len(dims) == 9
    keys = {d["dimension"] for d in dims}
    assert "quality_grade" in keys
    assert "close_reason" in keys
    assert all(d.get("bucket_label") for d in dims)


def test_highlights_take_profit_unit():
    pos = {
        "close_reason": "take_profit_50",
        "open_at": "2026-01-01T15:00:00+00:00",
        "close_at": "2026-01-15T20:00:00+00:00",
        "open_premium": 2.0,
        "contracts": 1,
        "realized_pnl": 100.0,
    }
    snap = {"delta": -0.12, "quality_grade": "A"}
    hl = build_position_highlights(pos, snap)
    texts = [h["text"] for h in hl["highlights"]]
    assert any("止盈" in t for t in texts)


def test_highlights_delta_high_lowlight():
    pos = {
        "close_reason": "manual",
        "open_at": "2026-01-01T15:00:00+00:00",
        "close_at": "2026-01-03T20:00:00+00:00",
    }
    snap = {"delta": -0.28, "quality_grade": "A"}
    hl = build_position_highlights(pos, snap)
    texts = [x["text"] for x in hl["lowlights"]]
    assert any("Delta" in t for t in texts)

