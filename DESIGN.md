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

（实施阶段补充组件清单与样式约定。）
