# ARCHITECTURE.md — 系统架构

> 详细定义见 **[设计规格 §3、§6](docs/superpowers/specs/2026-05-12-us-option-trading-assistant-design.md)**。

## 1. 进程与端口

- `server.py`：Flask、REST、静态前端、SSE；**7000**。
- `worker.py`：APScheduler、Jobs；**127.0.0.1:7001**（`/reload`、`/healthz`）。

## 2. 分层

- `app/core`：纯业务（策略、Greeks、结算）。
- `app/data`：`MarketDataProvider` 实现。
- `app/jobs`：调度与编排。
- `app/db`：SQLite schema 与 DAO。
- `app/api`：HTTP 路由。
- `app/notify`：事件总线与 SSE 桥接。

## 3. 数据流概要

（实施阶段补充 Mermaid 与端点列表。）
