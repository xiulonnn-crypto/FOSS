from __future__ import annotations

from pathlib import Path


FRONTEND_INDEX = Path(__file__).resolve().parents[1] / "frontend" / "index.html"


def test_screener_watch_pool_precedes_collapsed_underlying_watchlist():
    html = FRONTEND_INDEX.read_text()
    watch_grid = html.index('id="option-watch-grid"')
    watchlist_input = html.index('id="watchlist-input"')
    details_start = html.index('id="underlying-pool-panel"')
    details_tag_end = html.index(">", details_start)
    details_opening = html[details_start:details_tag_end]

    assert watch_grid < watchlist_input
    assert " open" not in details_opening


def test_screener_includes_entry_signal_filter_column_and_modal():
    html = FRONTEND_INDEX.read_text()

    assert 'id="option-pool-entry-signal-filter"' in html
    assert "开仓信号" in html
    assert 'id="entry-signal-modal"' in html
    assert "开仓决策卡" in html
