# ARCHITECTURE.md — 系统架构

> 详细定义见 **[设计规格 §3、§6](docs/superpowers/specs/2026-05-12-us-option-trading-assistant-design.md)**。

## 1. 进程与端口

- `server.py`：Flask、REST、静态前端、SSE；**7000**。
- `worker.py`：APScheduler、Jobs；**127.0.0.1:7001**（`/reload`、`/healthz`）。

## 2. 分层

- `app/core`：纯业务与复盘算法——`strategy`、`data_quality`、`features`、`greeks`、`settlement`、`types`；持仓标记 `position_mark` / `radar_snapshot`；入场快照 `open_snapshot`（RSI/布林带/状态特征等）；已平仓回放与 BS 反推 `entry_rehistory`；可选 Massive 日 K 增补 `massive_closed_enrichment` + OSI 编码 `option_ticker_osi`；美东与时区 `time_et`；`technicals`、`symbols`。
- `app/data`：`MarketDataProvider`（`provider_base`、`provider_yfinance`；另有 `provider_tradier` / `provider_ibkr` 占位）；`massive_client`（REST v2 aggs、进程内 5 次/分钟限速，供已平仓抽屉增补，不经由通用 Provider）。
- `app/jobs`：调度与编排。
- `app/db`：SQLite schema 与 DAO（`settings_default.json` 含 `integrations.massive_enrich_closed` 等）。
- `app/api`：HTTP 路由。
- `app/notify`：事件总线与 SSE 桥接。

**环境变量**：`MASSIVE_API_KEY` 仅来自仓库根 `.env`（`server.py` / `worker.py` 入口 `load_dotenv()`），不入库。已平仓 **POST `/api/positions/:id/close`** 与 **`POST /api/review/positions/:id/entry_recalc`** 在开关开启时 best-effort 合并 `open_snapshot.massive`。

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
  job_iv_snapshot
  /reload  → re-register jobs
  /healthz → {"ok":true}
```

**复盘 / 成交详情抽屉**：`GET /api/review/positions/:id/attribution` 使用 `open_snapshot`/候选希腊与 `radar_snapshots` 计算 BS 归因与 MAE/MFE；`GET .../snapshot` 返回 `open_snapshot`（含可选 `massive` 块）。已平仓「入场重算」走 `entry_rehistory.recalculate_closed_position_insights`（可重写雷达为日线 BS 回放）。批量「已平仓入场重算」API 已移除，仅保留单笔 `entry_recalc`。

**复盘 Phase 5**：`GET /api/review/summary` 支持 `since`/`until`/`symbols`/`pool`/`min_sample` 筛选，返回 `slices`（九维条件切片）、`performance_review`、`score_pnl_correlation`、`avg_realized_roe` 与 `avg_annualized_return`；`GET/POST /api/review/suggestions` 提供可应用设置建议（原子 merge + worker `/reload`）。核心模块：`review_analytics`、`review_suggestions`、`close_reason_norm`。

**扫描质量链路**：`job_screener` 在 `greeks.fill_greeks` 后调用 `strategy.score_csp_candidates_with_diagnostics`，同时写入候选快照与 `scan_runs.diagnostics`。`/api/scan/latest`、`/api/scan/run/:id`、`/api/scan/specific` 保持 `scan_latest_v2`，并在候选行上返回 flat 质量字段与 `data_quality` 嵌套对象；前端候选表、空状态与入场弹窗消费这些字段。

**阶段一特征工程链路**：`job_iv_snapshot` 每个交易日记录观察名单标的的本地 `market_iv_snapshots`（ATM/近 ATM `iv30`、OTM Put/Call `skew` 与 `^VIX`），并维护 `settings.iv_by_symbol` 的 252 点滚动序列。`app/core/features.py` 将历史收盘价、RV/IV 序列与最新 IV 快照组合成 `state_features`（RSI14、MACD 乖离、布林带 Z-Score、HV30、IV30、VRP、Skew、VIX、Regime）。`job_screener` 将该特征写入 `candidates.state_features`、`option_pool.state_features` 与 `feature_snapshots`；`open_snapshot` 在确认开仓时固化入场特征，供后续复盘与 Walk-Forward 使用。

**三层池链路**：`watchlist` 兼容旧观察名单并承担标的池；每轮扫描仍写 `candidates` 快照，同时 upsert `option_pool` 保存合约最新状态，并评估 `option_watchlist` 中的用户观察意图。新增 `app/core/option_pool.py` 纯规则模块，`routes_pool.py` 暴露 `/api/pool/*` 与 `/api/watch/options*`；观察池确认入场仍只写本地 `positions`，不连接券商、不自动下单。`worker.py` 启动时会执行一次轻量维护，调度器每日标记过期 pool/watch。

**智能开仓信号链路**：`app/core/entry_signal.py` 在策略 `score` 与数据质量之外生成独立的 `entry_signal_v1` 决策卡，输出 `OPENABLE / WAIT / REJECT / EXPIRED / UNKNOWN`、`decision_score`、分维度指标和可解释原因。`job_screener` 在同步 `option_pool` 后写入 `entry_signals` 历史快照，并把最新信号冗余到 `option_pool` 供 `/api/pool/options`、观察池和前端消费；开仓时 `open_snapshot` 保存当时的完整信号。该链路只做本地决策辅助，不预测收益、不自动下单。

**智能平仓与持仓动作建议链路**：`app/core/exit_signal.py` 将持仓标记结果升级为 `exit_signal_v1`，输出 `HOLD / HOLD_TO_EXPIRY / TAKE_PROFIT / ACCELERATE_TAKE_PROFIT / TIME_EXIT / DEFEND / EXPIRED / UNKNOWN`、紧迫度、建议出场原因、结构化 reasons 与旧 `radar_snapshots.signals` 兼容列表。`job_radar` 每轮继续写 `radar_snapshots`，同时写 `exit_signals` 最新快照和可行动事件；`routes_positions.py` 在 `/api/positions/marks`、建议详情、继续持有记录和平仓接口中消费该信号。用户确认平仓时，`positions.close_snapshot` 记录当时 `exit_signal` 与 mark；用户选择继续持有时写 `position_action_logs`。该链路只给动作建议，不自动下单、不自动滚仓。

## 4. 目录结构（实现后）

```
FOSS/
├── server.py              # Flask :7000
├── worker.py              # APScheduler :7001（入口 load_dotenv）
├── run.py                 # 一键启动
├── requirements.txt
├── .env.example            # 可选 TRADIER_*、MASSIVE_API_KEY 等（.gitignore 忽略 .env）
├── app/
│   ├── core/              # data_quality, features, option_pool, entry_signal, greeks, strategy, settlement, types, technicals, symbols, time_et,
│   │                      # position_mark, radar_snapshot, open_snapshot, entry_rehistory,
│   │                      # exit_signal, option_ticker_osi, massive_closed_enrichment
│   ├── data/              # provider_base/yfinance/tradier/ibkr, massive_client
│   ├── db/                # schema.sql, init_db.py, repo.py, paths.py, settings_default.json
│   ├── jobs/              # job_screener, job_radar, job_settlement, job_iv_history, job_iv_snapshot, scheduler_config
│   ├── api/               # routes_settings, routes_scan, routes_pool, routes_positions, routes_events, routes_review, internal_notify
│   └── notify/            # bus.py (EventBus)
├── frontend/
│   ├── index.html         # SPA shell (Tailwind CDN + Chart.js CDN)
│   ├── styles.css
│   └── js/app.js          # hash router, fetch, SSE, toast, screener/positions/review/settings
├── data/                  # options.db (runtime), snapshots/*.ndjson
└── tests/                 # pytest（约 150+ 用例，见 tests/test_*.py）
```
