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

```
Browser ──fetch/SSE──► server.py :7000
                           │
                           ├─ REST: /api/* ──► Repo ──► data/options.db (WAL)
                           ├─ SSE: /api/events/stream ──► EventBus(bus.py)
                           └─ POST /api/internal/notify (127.0.0.1 only)
                                       ▲
worker.py :7001            Jobs ───────┘
  APScheduler               │
  job_screener              ├─ INSERT events → DB
  job_radar                 └─ POST 127.0.0.1:7000/api/internal/notify
  job_settlement
  job_iv_history
  /reload  → re-register jobs
  /healthz → {"ok":true}
```

## 4. 目录结构（实现后）

```
FOSS/
├── server.py              # Flask :7000
├── worker.py              # APScheduler :7001
├── run.py                 # 一键启动
├── requirements.txt
├── app/
│   ├── core/              # greeks.py, strategy.py, settlement.py, types.py
│   ├── data/              # provider_base, provider_yfinance, stubs
│   ├── db/                # schema.sql, init_db.py, repo.py, settings_default.json
│   ├── jobs/              # job_screener, job_radar, job_settlement, job_iv_history, scheduler_config
│   ├── api/               # routes_settings, routes_scan, routes_positions, routes_events, routes_review, internal_notify
│   └── notify/            # bus.py (EventBus)
├── frontend/
│   ├── index.html         # SPA shell (Tailwind CDN + Chart.js CDN)
│   ├── styles.css
│   └── js/app.js          # hash router, fetch, SSE, toast, screener/positions/review/settings
├── data/                  # options.db (runtime), snapshots/*.ndjson
└── tests/                 # 61 tests
```
