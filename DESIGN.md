# DESIGN.md — UI 与前端

> 详细定义见 **[设计规格 §8](docs/superpowers/specs/2026-05-12-us-option-trading-assistant-design.md)**。

## 1. 技术栈

- 单页应用：hash 路由；Tailwind CSS；Chart.js；原生 JS 模块化于 `frontend/js/`。

## 2. 页面

- `#screener` — 期权池：标的池、合约池、观察池。
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
| 标的池 | `app.js` `renderUnderlyings()` | 展示 symbol、启用/暂停/归档 badge、标签、备注、最近扫描候选数与摘要 |
| 合约池表格 | `app.js` `renderCandidates()` | 展示最新 CSP Put 合约、质量列、状态 badge、入场/观察/忽略操作，年化ROI ≥25% 标绿；空结果提示运行扫描或放宽过滤 |
| 开仓信号 | `app.js` `entrySignalBadgeHtml()` / `openEntrySignalModal()` | 展示 `OPENABLE/WAIT/REJECT/UNKNOWN` badge、决策分、摘要和决策卡；决策卡分收益、风险、流动性、波动与时机、数据质量、原因列表 |
| 合约池筛选 | `index.html` + `app.js` | 支持状态、质量、开仓信号、最低评分、DTE 范围筛选，默认展示 `NEW/ACTIVE` |
| 观察池卡片 | `app.js` `renderOptionWatches()` | 展示 mid、目标 premium/score/margin、质量、开仓信号、DTE、距离目标差值、READY/EXPIRED/IGNORED/OPENED 状态，并支持保存目标、忽略、确认入场 |
| 扫描诊断 | `app.js` `renderScanDiagnostics()` | 候选表上方展示合约数、候选数、失败标的、质量分布与主要拒绝原因，长文本移动端自动换行 |
| 入场弹窗 | `app.js` `openEntryModal()` | 确认 strike/expiry，录入 premium+张数；B/unknown 质量候选和 WAIT/REJECT/UNKNOWN 开仓信号会显示提示，并随入场请求带入质量字段、entry signal 与可选 pool/watch 引用 |
| 持仓卡片 | `app.js` `loadPositions()` | 网格布局；展示现价、估算盈亏与 `exit_signal_v1` 动作 badge、摘要、关键指标；支持查看建议、继续持有、编辑、平仓 |
| 持仓动作建议弹窗 | `index.html` + `app.js` `openExitSignalModal()` | 展示 action/severity/urgency、收益/风险/时间/数据质量 reasons、当前值与阈值 |
| 继续持有记录弹窗 | `index.html` + `app.js` `openContinueHoldModal()` | 记录继续持有原因与备注，关联当前 `exit_signal_id` |
| 平仓弹窗 | `app.js` `openClosePositionModal()` | 从建议进入时自动带入建议出场原因、当前估价与 `exit_signal_id`；支持 `take_profit_fast`、`loss_breach` |
| 复盘页 | `app.js` `loadReview()` | 筛选器 + 汇总卡片 + 条件切片 + 表现体检 + 评分关联 + 设置建议应用；`/api/review/summary` 与 `/api/review/suggestions`；CSV 导出 |
| 设置表单 | `app.js` `renderSettingsForm()` | 5 个分组，数字/文本输入，保存触发热加载 |
