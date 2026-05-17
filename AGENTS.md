# AGENTS.md — AI 协作者入口

## 必读

1. **[设计规格](docs/superpowers/specs/2026-05-12-us-option-trading-assistant-design.md)** — 单一事实来源（MVP 范围、表结构、进程、UI、YAGNI）。
2. **[实施计划](docs/superpowers/plans/2026-05-12-us-option-trading-assistant-implementation.md)** — 落地任务与顺序（实施阶段维护）。

## 项目约束摘要

- 根目录即本仓库；独立项目，不与 `us-stock-trading-assistant` 合并代码。
- Python 3.11+；Flask + 原生 JS；默认行情 **yfinance**；Provider 可插拔。
- **分进程**：`server.py`（7000）与 `worker.py`（7001）；SQLite WAL。

## 正文

- **macOS 一键启动**：双击根目录 `启动期权助手.command`，脚本会先尝试释放 **监听 TCP 7000/7001** 的进程，再在仓库根执行 `python3 run.py`。（开发时避免单独起 `server.py` / `worker.py` 与 `run.py` 叠床架屋导致端口冲突。）

（实施阶段在此补充：常用命令、测试入口、目录约定。）
