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


def test_screener_surfaces_phase_one_state_features():
    html = FRONTEND_INDEX.read_text()
    js = (Path(__file__).resolve().parents[1] / "frontend" / "js" / "app.js").read_text()

    assert "状态特征" in html
    assert "stateFeaturesHtml" in js
    assert "getStateFeatures" in js
    assert "VIX待定" in js
    assert "body.state_features = stateFeatures" in js


def test_screener_specific_search_candidates_support_watch_action():
    js = (Path(__file__).resolve().parents[1] / "frontend" / "js" / "app.js").read_text()
    assert "source === 'specific'" in js
    assert "watchOptionPool" in js
    assert "_screenerCandidateSource" in js


def test_screener_watch_pool_hides_terminal_status_cards():
    js = (Path(__file__).resolve().parents[1] / "frontend" / "js" / "app.js").read_text()
    assert "ACTIVE_WATCH_STATUSES = ['WATCHING', 'READY']" in js
    assert "filterActiveWatchRows" in js
    assert "status=${ACTIVE_WATCH_STATUSES.join(',')}" in js


def test_screener_auto_refresh_mirrors_positions_pattern():
    """While the user stays on #screener, the page must self-refresh every minute.

    Logic mirrors `startPositionsAutoRefresh`: a 60-second `setInterval`
    started on entering the page and stopped when leaving, hitting the
    new `/api/screener/marks` endpoint that returns refreshed underlying
    spot + rebuilt entry_signal so 决策卡 reads the latest market data.
    """
    js = (Path(__file__).resolve().parents[1] / "frontend" / "js" / "app.js").read_text()

    assert "function startScreenerAutoRefresh()" in js
    assert "function stopScreenerAutoRefresh()" in js
    assert "async function refreshScreenerMarks(" in js

    # 60-second polling cadence — same as positions auto-refresh.
    assert "_screenerAutoRefreshInterval = setInterval" in js
    assert "60_000" in js

    # `showPage` must start the timer on #screener and stop it on leave.
    assert "if (page === 'screener') startScreenerAutoRefresh()" in js
    assert "if (page !== 'screener') stopScreenerAutoRefresh()" in js

    # Endpoint URL parity with backend route.
    assert "/api/screener/marks" in js

    # Active option-pool filter must be preserved across the auto-refresh
    # so the rendered table doesn't drift from what the user is looking at.
    assert "optionPoolFilterQuery()" in js
    assert "ACTIVE_WATCH_STATUSES.join(',')" in js


def test_screener_marks_refresh_writes_global_quote_label():
    """Each /api/screener/marks tick must update the 顶部「刷新行情」按钮右侧的「行情时间」标签
    (`#global-quote-as-of`)，否则用户看不到上次刷新发生在什么时候。
    """
    js = (Path(__file__).resolve().parents[1] / "frontend" / "js" / "app.js").read_text()

    # refreshScreenerMarks must propagate quoted_at to the shared label.
    refresh_fn_start = js.index("async function refreshScreenerMarks(")
    refresh_fn_body = js[refresh_fn_start:refresh_fn_start + 2500]
    assert "updateGlobalQuoteLabel(" in refresh_fn_body, (
        "refreshScreenerMarks 应在拉到 quoted_at 后调用 updateGlobalQuoteLabel"
    )
    assert ".quoted_at" in refresh_fn_body


def test_screener_kicks_marks_refresh_soon_after_entering_page():
    """Entering #screener should populate the quote label without waiting 60s
    for the first interval tick — schedule an early one-shot refresh."""
    js = (Path(__file__).resolve().parents[1] / "frontend" / "js" / "app.js").read_text()

    start_fn = js.index("function startScreenerAutoRefresh()")
    body = js[start_fn:start_fn + 1500]
    # A setTimeout — much shorter than 60s — must arm the first refresh.
    assert "setTimeout" in body, (
        "startScreenerAutoRefresh 应安排一次延迟极短的首次 refreshScreenerMarks，"
        "避免用户进入 #screener 后要等满 60 秒才看到行情时间。"
    )


def test_entry_signal_modal_shows_data_timestamp():
    """决策卡（#screener 开仓决策卡）必须显示 entry_signal.generated_at —— 即「这一卡上面所有数字是哪一刻拉到的」。

    用同一套 ET 格式器（formatQuotedAt / formatEtDatetime）保持与顶栏「行情时间」标签一致；
    缺失 generated_at 时不渲染该行，避免出现裸的 ‘—’。
    """
    js = (Path(__file__).resolve().parents[1] / "frontend" / "js" / "app.js").read_text()

    # 渲染逻辑提取到了 _renderEntrySignalBody；在该辅助函数体内检查
    render_start = js.index("function _renderEntrySignalBody(row")
    render_body = js[render_start:render_start + 3500]

    assert "数据时点" in render_body, "决策卡应展示『数据时点』标签"
    assert "signal.generated_at" in render_body, "应读取 signal.generated_at 渲染"
    assert "formatQuotedAt(signal.generated_at)" in render_body, (
        "应复用 formatQuotedAt，与顶栏行情时间格式保持一致"
    )


def test_refresh_quotes_button_targets_active_page():
    """顶部「刷新行情」按钮按当前页面语义路由：

    - #screener  → refreshScreenerMarks (刷池/观察池/标的池 + 写 quote label)
    - #positions → loadPositions
    其他页保留原有持仓 marks 心跳作为后备，避免行情时间标签空白。
    """
    js = (Path(__file__).resolve().parents[1] / "frontend" / "js" / "app.js").read_text()

    handler_start = js.index("getElementById('btn-refresh-quotes').addEventListener('click'")
    handler_body = js[handler_start:handler_start + 1600]
    assert "_currentPage === 'screener'" in handler_body
    assert "refreshScreenerMarks(" in handler_body
    assert "_currentPage === 'positions'" in handler_body
    assert "loadPositions(" in handler_body


def test_screener_decision_card_mirrors_review_entry_snapshot_labels():
    """#screener 决策卡的标的价 / 布林带行须与 #review 入场环境快照保持同名同色。

    Review snapshot uses ``入场标的价 $XX.XX``、``距布林带下轨`` 并以
    ``text-rose-400`` 高亮跌破下轨。决策卡风险卡 / 时机卡必须复用同一套
    label + 颜色，避免双视图描述同一条入场环境时出现术语漂移。
    """
    js = (Path(__file__).resolve().parents[1] / "frontend" / "js" / "app.js").read_text()

    assert "['入场标的价'," in js, "决策卡风险卡应有独立『入场标的价』行"
    assert "['行权价'," in js, "决策卡风险卡应有独立『行权价』行"
    assert "现价 / 行权价" not in js, "拆行后旧的合并标签不应再出现"

    assert "距布林带下轨" in js, "时机卡应使用与 review 一致的『距布林带下轨』命名"
    assert "布林距离" not in js, "旧的『布林距离』标签应已替换"

    # 负值高亮（跌破下轨）必须与 review 入场快照同色。
    assert "bb != null && bb < 0 ? 'text-rose-400'" in js


def test_modal_refresh_rerenders_screener_grids_after_patch():
    """决策卡刷新成功后必须立即重渲染 screener 的合约池表格和观察池卡片。

    _patchCachedOptionRow 仅更新内存数组，不触发 DOM 更新。
    openEntrySignalModal 的成功路径（freshRow 不为 null）必须在调用
    _patchCachedOptionRow 之后紧接着：
      1. 调用 renderOptionWatches(_lastWatchRows)    — 观察池卡片
      2. 调用 renderCandidates(...)                  — 合约池表格

    否则用户在决策卡更新后切回 #screener，仍然看到旧的 Premium/Greeks 数值。
    """
    js = (Path(__file__).resolve().parents[1] / "frontend" / "js" / "app.js").read_text()

    fn_start = js.index("async function openEntrySignalModal(")
    fn_body = js[fn_start:fn_start + 3000]

    # 成功路径中调用 _patchCachedOptionRow 之后，必须触发两类渲染
    patch_pos = fn_body.index("_patchCachedOptionRow(freshRow)")
    after_patch = fn_body[patch_pos:]

    assert "renderOptionWatches(_lastWatchRows)" in after_patch, (
        "openEntrySignalModal 成功路径在 _patchCachedOptionRow 之后缺少 "
        "renderOptionWatches(_lastWatchRows)。"
        "内存已更新但 DOM 未重渲染，用户看到的观察池卡片仍是旧数据。"
    )
    assert "renderCandidates(" in after_patch, (
        "openEntrySignalModal 成功路径在 _patchCachedOptionRow 之后缺少 "
        "renderCandidates(...)。"
        "内存已更新但 DOM 未重渲染，用户看到的合约池表格仍是旧数据。"
    )


def test_watch_card_passes_option_pool_id_to_entry_signal_modal():
    """观察池「查看决策卡」按钮必须在调用 openEntrySignalModal 时传入 option_pool_id。

    根因：watch.option 来自 _option_watch_from_row，该函数在剥离 option_* 前缀时
    显式排除了 option_pool_id（行 137: key != "option_pool_id"），因此
    watch.option 内无此字段。option_pool_id 留在 watch 顶层。

    若按钮只传 { ...option, watch_id: id }，openEntrySignalModal 找不到
    option_pool_id（undefined → null），直接走「展示缓存数据」分支，
    不触发 /api/pool/options/<id>/refresh 端点，观察卡始终不更新。

    修复：在 renderOptionWatches 构造的调用对象中补填
    option_pool_id: Number(watch.option_pool_id ?? option.id)。
    """
    js = (Path(__file__).resolve().parents[1] / "frontend" / "js" / "app.js").read_text()

    render_start = js.index("function renderOptionWatches(")
    render_body = js[render_start:render_start + 2000]

    assert "option_pool_id" in render_body, (
        "renderOptionWatches 构造的 openEntrySignalModal 调用缺少 option_pool_id。"
        "watch.option 内无此字段（被 repo._option_watch_from_row 排除在外）；"
        "必须从 watch.option_pool_id 或 option.id 补填，"
        "否则观察卡点击后只展示缓存数据，不触发 /refresh 端点。"
    )
