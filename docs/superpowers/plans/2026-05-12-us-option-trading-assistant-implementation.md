# us-option-trading-assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现本地期权卖方 MVP（CSP）：yfinance 数据源、SQLite 持久化、Flask+SSE（7000）与 Worker+APScheduler（7001）、四页 SPA、筛选/雷达/结算/复盘闭环。

**Architecture:** `app/core` 纯函数策略与数学；`app/data` 实现 `MarketDataProvider`；`app/jobs` 仅编排；`server` 与 `worker` 分进程，事件经 DB + `POST /api/internal/notify` 推 SSE。

**Tech Stack:** Python 3.11+、Flask 3、flask-cors、APScheduler、SQLAlchemy（JobStore）、yfinance、pandas、numpy、scipy、requests、requests-cache、python-dateutil、pytest。

**Spec:** [2026-05-12-us-option-trading-assistant-design.md](../specs/2026-05-12-us-option-trading-assistant-design.md)

---

## Spec coverage（自检）

| 规格章节 | 对应 Task |
|----------|-----------|
| §1 目录与端口 | Task 1, 10, 11 |
| §2 状态机与业务 | Task 5, 7, 8, 9 |
| §3–§4 DB | Task 2, 3 |
| §5 Provider | Task 4 |
| §6 调度与通知 | Task 8, 9, 10 |
| §7 策略与 settings | Task 5, 6, 9 |
| §8 UI | Task 11 |
| §9 部署与测试 | Task 1, 12, 13 |

---

## File map（将创建/修改）

| 路径 | 职责 |
|------|------|
| `requirements.txt` | 依赖锁定 |
| `.env.example` | Tradier 等占位 |
| `app/__init__.py` | 包标记 |
| `app/db/schema.sql` | §4.3 DDL |
| `app/db/init_db.py` | 建库、WAL、`PRAGMA` |
| `app/db/repo.py` | settings/watchlist/scan_runs/candidates/positions/radar_snapshots/events CRUD |
| `app/db/settings_default.json` | 默认 `settings` 单行 JSON（与 spec §7.4 一致） |
| `app/core/types.py` | `OptionContract`, `Quote`, `Settings` dataclass |
| `app/core/greeks.py` | BS Delta/Gamma/Theta/Vega |
| `app/core/strategy.py` | 硬过滤、评分、`evaluate_exit_signals` |
| `app/core/settlement.py` | 到期 OTM/ITM 判定、`close_reason` |
| `app/data/provider_base.py` | Protocol / ABC |
| `app/data/provider_yfinance.py` | MVP 实现 |
| `app/data/provider_tradier.py` | `NotImplementedError` 或 stub |
| `app/data/provider_ibkr.py` | stub |
| `app/jobs/scheduler_config.py` | 从 DB 读 schedule、注册 jobs |
| `app/jobs/job_screener.py` | 扫描写 candidates + snapshot + events |
| `app/jobs/job_radar.py` | OPEN 持仓雷达 |
| `app/jobs/job_settlement.py` | 16:30 ET |
| `app/jobs/job_iv_history.py` | RV 缓存 |
| `app/notify/bus.py` | 内存 pub/sub |
| `worker.py` | APScheduler + Flask 微型 app `:7001` `/reload` `/healthz` |
| `server.py` | Flask 主应用 `:7000`、静态、`/api/*`、SSE |
| `app/api/routes_settings.py` | GET/POST settings + 转发 reload |
| `app/api/routes_scan.py` | POST `/api/scan/run` |
| `app/api/routes_positions.py` | CRUD positions |
| `app/api/routes_events.py` | list、ack、SSE stream |
| `app/api/internal_notify.py` | `POST /api/internal/notify` |
| `run.py` | 启动链 |
| `frontend/index.html` | SPA shell |
| `frontend/styles.css` | 可选 |
| `frontend/js/*.js` | 路由、表格、SSE、Toast |
| `tests/test_greeks.py` | BS 已知值断言 |
| `tests/test_strategy.py` | 过滤与评分、出场信号 |
| `tests/test_settlement.py` | OTM/ASSIGNED |
| `tests/test_repo.py` | SQLite `:memory:` |
| `tests/conftest.py` | fixtures |

---

### Task 1: 依赖与包布局

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `app/__init__.py`

- [ ] **Step 1:** 写入 `requirements.txt`（版本下限与 spec 一致）：

```
flask>=3.0
flask-cors>=4.0
apscheduler>=3.10
sqlalchemy>=2.0
yfinance>=0.2.36
pandas>=2.0
numpy>=1.26
scipy>=1.10
requests>=2.31
requests-cache>=1.0
python-dateutil>=2.8
python-dotenv>=1.0
pytest>=8.0
```

- [ ] **Step 2:** `.env.example` 内容：

```
# 可选：Tradier
# TRADIER_API_KEY=
# TRADIER_BASE_URL=https://sandbox.tradier.com/v1
```

- [ ] **Step 3:** `app/__init__.py` 可为空或 `__version__ = "0.1.0"`。

- [ ] **Step 4:** 运行 `pip3 install -r requirements.txt`，确认无解析错误。

- [ ] **Step 5:** `git add requirements.txt .env.example app/__init__.py && git commit -m "chore: bootstrap deps and app package"`

---

### Task 2: 数据库 schema 与初始化

**Files:**
- Create: `app/db/schema.sql`（从 spec §4.3 原样复制 DDL）
- Create: `app/db/init_db.py`
- Create: `app/db/settings_default.json`（与 spec §7.4 JSON 一致，单行或美化均可；加载后合并写入 `settings` 表 `key='app'`）

- [ ] **Step 1:** `init_db.py` 函数 `init_database(db_path: Path)`：`connect` → `executescript(schema.sql)` → `PRAGMA journal_mode=WAL` → 若 `settings` 无 `app` 键则插入默认 JSON。

- [ ] **Step 2:** 手动运行 `python3 -c "from app.db.init_db import init_database; from pathlib import Path; init_database(Path('data/options.db'))"`（先 `mkdir -p data`），用 `sqlite3 data/options.db ".tables"` 验证 7 表存在。

- [ ] **Step 3:** `git add app/db/ && git commit -m "feat(db): schema and init"`

---

### Task 3: Repository 层

**Files:**
- Create: `app/db/repo.py`
- Create: `tests/test_repo.py`

- [ ] **Step 1:** 在 `test_repo.py` 使用 `sqlite3.connect(":memory:")` 执行 `schema.sql`，测试 `insert_scan_run`、`insert_candidates`、`insert_position`、`insert_event`、`list_unread_events`。

- [ ] **Step 2:** 实现 `repo.py`：所有 SQL 用参数化查询；`get_settings()` 返回 dict；`save_settings(dict)` 序列化 JSON；`merge_settings` 支持部分更新。

- [ ] **Step 3:** `pytest tests/test_repo.py -v` 全绿。

- [ ] **Step 4:** `git add app/db/repo.py tests/test_repo.py && git commit -m "feat(db): repository layer"`

---

### Task 4: MarketDataProvider（yfinance）

**Files:**
- Create: `app/core/types.py`
- Create: `app/data/provider_base.py`
- Create: `app/data/provider_yfinance.py`
- Create: `app/data/provider_tradier.py`（raise `NotImplementedError`）
- Create: `app/data/provider_ibkr.py`（空类 stub）
- Create: `tests/test_provider_yfinance.py`（`unittest.mock` patch `yfinance` 返回固定 DataFrame，测解析与裁剪逻辑）

- [ ] **Step 1:** `types.py` 定义 `OptionContract`, `Quote` 与 spec §5 对齐字段。

- [ ] **Step 2:** `provider_yfinance.get_historical_close`：若 `day` 为周末，回退到最近上一交易日收盘价（用 pandas BDay 或 dateutil）。

- [ ] **Step 3:** `get_next_earnings`：`Ticker(sym).calendar` 安全访问，异常返回 `None`。

- [ ] **Step 4:** 单测覆盖链裁剪（DTE、strike 范围）与 bid/ask 缺失分支。

- [ ] **Step 5:** `pytest tests/test_provider_yfinance.py -v`；`git commit -m "feat(data): yfinance provider and types"`

---

### Task 5: Greeks 与策略核心（TDD）

**Files:**
- Create: `app/core/greeks.py`
- Create: `tests/test_greeks.py`
- Create: `app/core/strategy.py`
- Create: `tests/test_strategy.py`

- [ ] **Step 1:** `test_greeks.py` 使用已知 ATM 参数断言 Delta 约在 0.5（Call）或 Put 对称；Theta/Vega 符号合理。

```python
# tests/test_greeks.py 示例断言结构（数值按实现调参）
def test_put_delta_negative():
    from app.core import greeks
    d = greeks.black_scholes_delta(
        spot=100.0, strike=100.0, rate=0.045, iv=0.25, t_years=30 / 365.0, right="P"
    )
    assert -0.55 < d < -0.45
```

- [ ] **Step 2:** 实现 `greeks.py`（Put 用 d1/d2 标准式）。

- [ ] **Step 3:** `test_strategy.py`：构造 3 条假 `OptionContract`，验证硬过滤（spread、dte、delta）与 `score` 排序。

- [ ] **Step 4:** `evaluate_exit_signals(position, quote, chain_mid, settings)` 返回与 spec §7.3 一致的 signal id 列表。

- [ ] **Step 5:** `pytest tests/test_greeks.py tests/test_strategy.py -v`；`git commit -m "feat(core): greeks and csp strategy"`

---

### Task 6: 结算逻辑

**Files:**
- Create: `app/core/settlement.py`
- Create: `tests/test_settlement.py`

- [ ] **Step 1:** `settle_short_put(spot_close, strike) -> Literal["expired_otm","assigned"]`：spot_close > strike → OTM 卖方全收；否则 assigned。

- [ ] **Step 2:** `pytest tests/test_settlement.py -v`；`git commit -m "feat(core): settlement rules"`

---

### Task 7: Jobs（screener / radar / settlement / iv）

**Files:**
- Create: `app/jobs/job_screener.py`
- Create: `app/jobs/job_radar.py`
- Create: `app/jobs/job_settlement.py`
- Create: `app/jobs/job_iv_history.py`
- Create: `app/jobs/scheduler_config.py`
- Create: `tests/test_jobs_integration.py`（FakeProvider + `:memory:` repo）

- [ ] **Step 1:** `job_screener`：读 `watchlist` + `settings` → 拉链 → `strategy` → 写 `scan_runs`、`candidates`、NDJSON 路径更新、`events` 插入 `screener.completed`。

- [ ] **Step 2:** `job_radar`：遍历 `OPEN` → 写 `radar_snapshots` → 新信号插入 `events` → `httpx`/`urllib` POST `http://127.0.0.1:7000/api/internal/notify`（失败仅 log）。

- [ ] **Step 3:** `job_settlement`：找当日到期 `OPEN` → `get_historical_close` → 更新 `state` 与 `close_reason`。

- [ ] **Step 4:** `job_iv_history`：写简单缓存表或 `settings` 子键 `rv_cache:{symbol}`（若不愿加表，可用 `settings` key per symbol，注意 JSON 大小；**优先**新增表 `iv_cache(symbol TEXT PRIMARY KEY, rv REAL, updated_at TEXT)` 在实施时若更简单可放 `schema.sql` v2 —— **MVP 用 `settings` 内嵌 `rv_by_symbol` dict 即可**，并在本任务更新 `schema.sql` 若采用新表则同步 spec，此处 **YAGNI：用 settings JSON 字段 `rv_by_symbol`**）。

- [ ] **Step 5:** 集成测试 FakeProvider 返回两条链，`job_screener` 后 `candidates` 行数 > 0。

- [ ] **Step 6:** `git commit -m "feat(jobs): screener radar settlement iv"`

---

### Task 8: Worker 进程

**Files:**
- Create: `worker.py`

- [ ] **Step 1:** 用 `SQLAlchemyJobStore(url='sqlite:///data/options.db')` 注册 APScheduler（与 spec §6.4 一致）；`misfire_grace_time=300`。

- [ ] **Step 2:** 内嵌 Flask `app_internal` 仅 `127.0.0.1:7001`，路由 `POST /reload`（重读 settings、reschedule）、`GET /healthz` 返回 `{"ok":true}`。

- [ ] **Step 3:** `if __name__ == "__main__"`：`threading` 起 Flask，`BlockingScheduler.start()` 或反之文档化。

- [ ] **Step 4:** 手动：`python3 worker.py` 后 `curl -s http://127.0.0.1:7001/healthz`。

- [ ] **Step 5:** `git commit -m "feat(worker): scheduler and reload http"`

---

### Task 9: Notify 与 Server

**Files:**
- Create: `app/notify/bus.py`
- Create: `server.py`
- Create: `app/api/internal_notify.py`
- Create: `app/api/routes_events.py`
- Create: `app/api/routes_settings.py`
- Create: `app/api/routes_scan.py`
- Create: `app/api/routes_positions.py`
- Create: `tests/test_api_flask.py`

- [ ] **Step 1:** `EventBus`：`subscribe(generator)` 用于 SSE；`publish(event_dict)`。

- [ ] **Step 2:** `POST /api/internal/notify`：检查 `remote_addr == 127.0.0.1`，body `{"id": <event_id>}`，查 DB → `bus.publish`。

- [ ] **Step 3:** `GET /api/events/stream`：`text/event-stream`，心跳 comment 每 30s。

- [ ] **Step 4:** 其余 REST：settings、scan、positions、events list/ack。

- [ ] **Step 5:** `test_api_flask.py` 使用 `app.test_client()` 测 settings GET、notify 403 对外网 IP（模拟）。

- [ ] **Step 6:** `git commit -m "feat(server): flask api sse and internal notify"`

---

### Task 10: 前端 SPA

**Files:**
- Create: `frontend/index.html`
- Create: `frontend/js/app.js`（hash 路由、fetch、EventSource）
- Create: `frontend/js/screener.js` / `positions.js` / `review.js` / `settings.js`（可合并为单文件若 YAGNI）
- Modify: `server.py` 静态挂载 `/` → `frontend/`

- [ ] **Step 1:** Tailwind CDN + Chart.js CDN；四路由占位 DOM。

- [ ] **Step 2:** 铃铛下拉拉 `GET /api/events?limit=20`；Toast 容器。

- [ ] **Step 3:** `#settings` 表单绑定 JSON 字段与保存按钮。

- [ ] **Step 4:** 手动浏览器验证 SSE 绿点与保存触发 reload（worker 日志可见 reschedule）。

- [ ] **Step 5:** `git commit -m "feat(ui): spa shell and sse client"`

---

### Task 11: run.py 与启动脚本

**Files:**
- Create: `run.py`
- Create: `启动期权助手.command`（`cd` 到项目目录，`python3 run.py`）

- [ ] **Step 1:** `run.py`：检查版本 → `pip3 install -r requirements.txt`（可选 `--no-deps` 快速模式由 env 控制）→ `init_database` → `subprocess.Popen` worker → `subprocess` 或 `os.exec` 前台 server；写 `data/worker.pid`；`atexit` / signal 杀 worker。

- [ ] **Step 2:** macOS 双击 command 文件 UTF-8。

- [ ] **Step 3:** `git commit -m "chore(run): one-command startup"`

---

### Task 12: 文档回填与 ARCHITECTURE 图表

**Files:**
- Modify: `ARCHITECTURE.md`, `TECHNICAL.md`, `DESIGN.md`, `README.md`

- [ ] **Step 1:** 将实际端点表、环境变量、目录树从实现抄回文档。

- [ ] **Step 2:** `git commit -m "docs: align with implementation"`

---

### Task 13: 复盘 API 与 CSV 导出

**Files:**
- Create: `app/api/routes_review.py`
- Modify: `server.py` 注册蓝图

- [ ] **Step 1:** `GET /api/review/summary`：四总指标 + `close_reason` 分组聚合 SQL。

- [ ] **Step 2:** `GET /api/review/positions.csv`：`text/csv`。

- [ ] **Step 3:** `tests/test_review_api.py`。

- [ ] **Step 4:** `git commit -m "feat(api): review summary and csv"`

---

## Plan self-review

- **Spec coverage:** 各节均有对应 Task；复盘 CSV 在 Task 13 显式补齐（spec §8.5）。
- **Placeholder scan:** 本计划无 TBD/TODO；Task 7 Step 4 已选定 `rv_by_symbol` 存 settings 避免未定表结构。
- **Consistency:** `close_reason` / 信号 id 与 spec §4.4、§7.3 一致；端口 7000/7001 全文一致。

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-12-us-option-trading-assistant-implementation.md`. Two execution options:**

1. **Subagent-Driven (recommended)** — 每任务派生子代理、任务间复核、迭代快  
2. **Inline Execution** — 本会话用 `executing-plans` 批量执行并设检查点  

**Which approach?**（由你或下一会话的执行者选择。）

---

## Post-Plan 追加功能记录

### 决策卡（决策卡）点击刷新 *(2026-05-29 完成)*

**需求：** 点击 screener 候选表格或观察池的「决策卡」按钮时，单独拉取最新期权行情（含 Premium + Greeks），局部更新 modal。

**实现：**

| 变更 | 路径 | 说明 |
|------|------|------|
| 新 API 端点 | `app/api/routes_pool.py` | `GET /api/pool/options/<id>/refresh[?watch_id=X]`：调用 `get_quote` + `get_option_chain` → `derive_csp_candidate_row` → 重建 `entry_signal`，返回 `{ schema, quoted_at, option, chain_refreshed }` |
| 前端 loading 骨架 | `frontend/js/app.js` | 新增 `_renderEntrySignalLoading()` — spinner + 「正在拉取最新行情…」 |
| 前端 modal 渲染 | `frontend/js/app.js` | `openEntrySignalModal` 改为 async；流程：loading → fetch → 成功显示 `✓ 已刷新 · 数据时点` / 失败显示 `⚠ 行情刷新失败 + 缓存数据` |
| 缓存同步 | `frontend/js/app.js` | `_patchCachedOptionRow(freshRow)` 将刷新数据写回 `_lastPoolRows` / `_screenerCandidateRows` / `_lastWatchRows` |
| watch 网格 | `frontend/js/app.js` | 「查看决策卡」按钮补传 `watch_id`，使 entry_signal 重建时携带 watch 上下文 |

**UX 模型选择：**「结果明确（loading-first）」—— modal 始终只在拿到 API 结果后才展示数据，避免用户混淆缓存数据与实时数据。iv_rank 历史数据（252 天）不在点击时重拉（成本过高，YAGNI）。

**测试：** 325 passed，0 failed（含 `test_screener_marks_api.py`、`test_frontend_pool_layout.py`、`test_entry_signal_core.py`）。

**经验教训（已更新 brainstorming 技能）：** 涉及「点击打开含网络请求的 modal」的设计，必须在 brainstorming 阶段明确问：「等待期间展示缓存数据还是 Loading 状态？」否则实现者会默认 optimistic，用户接受后发现不符合预期，导致二次迭代。

### 决策卡刷新 Bug Fix — 观察池路径缺 option_pool_id *(2026-05-29)*

**症状：** 观察池点击「查看决策卡」不触发刷新，始终展示缓存数据。

**根因：** `_option_watch_from_row`（repo.py:137）在剥离 `option_*` 前缀时显式排除 `option_pool_id`（保留在 watch 顶层）。`renderOptionWatches` 构造 `openEntrySignalModal` 调用时只传 `{ ...option, watch_id: id }`，`watch.option` 内无 `option_pool_id`，modal 走缓存路径。

**修复：** `app.js line 889`，补填 `option_pool_id: Number(watch.option_pool_id ?? option.id)`（一行）。

**教训 — 多入口函数的完整性审计：** 实现「决策卡刷新」时只测试了合约池入口，未覆盖观察池入口。规则：当一个关键函数（如 `openEntrySignalModal`）被 N 个入口调用时，每个入口必须有独立测试验证关键字段（`option_pool_id`）的传递。COUNT 入口数 → 每个入口一个 test。

### 决策卡刷新 Bug Fix — 内存更新后未重渲染 screener 页面 *(2026-05-29)*

**症状：** 决策卡刷新成功后，#screener 合约池表格和观察池卡片仍显示旧的 Premium/Greeks 数据，需手动点「刷新行情」才能看到新值。

**根因：** `_patchCachedOptionRow(freshRow)` 只更新内存数组（`_lastPoolRows`、`_screenerCandidateRows`、`_lastWatchRows`），调用后 **未触发任何 DOM 重渲染**。对比 `refreshScreenerMarks` 在更新内存后立即调用了 `renderCandidates` 和 `renderOptionWatches`，`openEntrySignalModal` 的成功路径漏掉了这两个调用。

**修复：** `frontend/js/app.js`，在 `_patchCachedOptionRow(freshRow)` 调用后追加（3 行）：
```javascript
renderOptionWatches(_lastWatchRows);
if (_screenerCandidateSource === 'pool') {
  renderCandidates(_screenerCandidateRows, _lastScanRun, { source: 'pool' });
}
```

**测试：** 新增 `test_modal_refresh_rerenders_screener_grids_after_patch`（RED → GREEN）；20 个前端 + 核心测试全通过。

**教训 — 内存-DOM 失步规则（Memory-DOM sync gap）：** 每当调用名为 "patch/update/sync/write" 的内存更新函数之后，必须明确问：**「谁负责把这份内存变化映射回 DOM？」**
- 若答案是「调用方自己」：必须在调用后显式写 re-render 调用；
- 若答案是「这个函数内部」：函数名应包含 "render/refresh/flush"，并在 docstring 中标注。
若未明确回答这个问题，DOM 与内存将悄无声息地失步，无任何报错，只有用户发现数据是旧的。
