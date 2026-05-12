# DESIGN.md — UI 与前端

> 详细定义见 **[设计规格 §8](docs/superpowers/specs/2026-05-12-us-option-trading-assistant-design.md)**。

## 1. 技术栈

- 单页应用：hash 路由；Tailwind CSS；Chart.js；原生 JS 模块化于 `frontend/js/`。

## 2. 页面

- `#screener` — 候选筛选。
- `#positions` — 持仓雷达。
- `#review` — 复盘。
- `#settings` — 系统设置。
- 事件：侧栏铃铛下拉（无独立事件页）。

## 3. 全局组件

- Toast、SSE 状态点、Provider 延迟徽章。

## 4. 实现清单

| 组件 | 文件 | 说明 |
|------|------|------|
| SPA shell | `frontend/index.html` | Tailwind CDN + Chart.js CDN，4 个 `<section>` 占位 |
| 路由 + 核心逻辑 | `frontend/js/app.js` | hash router，`showPage()`，`apiFetch()` |
| 铃铛下拉 | `app.js` `refreshBell()` | 拉 `/api/events?unread=true`，展示最近 20 条 |
| Toast | `app.js` `toast()` | 堆叠上限 5；`danger` 级需手动关闭 |
| SSE 状态点 | `app.js` `connectSSE()` | 绿点=已连接，红点=断开（5s 重连） |
| 筛选表格 | `app.js` `renderCandidates()` | 14 列，行内入场按钮，年化ROI ≥25% 标绿 |
| 入场弹窗 | `app.js` `openEntryModal()` | 确认 strike/expiry，录入 premium+张数 |
| 持仓卡片 | `app.js` `loadPositions()` | 网格布局；平仓/雷达快照按钮 |
| 复盘四指标 | `app.js` `loadReview()` | 调 `/api/review/summary`；CSV 导出链接 |
| 设置表单 | `app.js` `renderSettingsForm()` | 5 个分组，数字/文本输入，保存触发热加载 |
