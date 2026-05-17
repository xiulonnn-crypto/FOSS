# TECHNICAL.md — 数据与策略实现细节

> 详细定义见 **[设计规格 §4、§5、§7](docs/superpowers/specs/2026-05-12-us-option-trading-assistant-design.md)**。

## 1. 数据库

- 路径：`data/options.db`（WAL）；建表语句见规格 §4.3。
- 快照：`data/snapshots/*.ndjson`（不入版本库）。
- `watchlist` 继续兼容旧观察名单，并新增标的池字段：`pool_status`、`tags`、`notes`、`last_scanned_at`、`last_candidate_count`、`last_pool_summary`。`ACTIVE` 同步 `enabled=1`，`PAUSED/ARCHIVED` 同步 `enabled=0`。
- `candidates` 可保存免费行情质量字段：`quality_grade`、`quality_score`、`quality_flags`、`quote_age_seconds`、`greeks_source`、`iv_rank_source`；旧行读取时按 `unknown` 兼容。
- `scan_runs.diagnostics` 保存 JSON 文本，用于记录每轮扫描的质量分布、拒绝原因、失败标的与合约计数。
- `option_pool` 保存 CSP Put 合约最新状态，唯一键 `(symbol, expiration, strike, right)`，包含最新 quote/Greeks/质量/评分指标、`first_seen_at`、`last_seen_at`、`last_scan_run_id`、`latest_candidate_id`、`missed_scan_count`、`status`。
- `option_watchlist` 保存用户观察意图，关联 `option_pool.id`，状态为 `WATCHING | READY | IGNORED | OPENED | EXPIRED`，含目标 premium/score/margin、备注、`last_evaluated_at`、`last_signal`。
- `entry_signals` 保存智能开仓信号历史快照；`option_pool` 冗余最新 `entry_signal_status`、`entry_signal_score`、`entry_signal_summary` 与完整 payload，便于列表筛选和前端展示。
- `exit_signals` 保存智能平仓/持仓动作建议信号历史快照；同一持仓仅一条 `is_latest=1`，并把最新 `exit_signal_action/severity/score/summary/generated_at/payload` 冗余到 `positions`。
- `position_action_logs` 保存用户动作记录，当前用于 `CONTINUE`（继续持有原因）与 `CLOSE_CONFIRMED`（确认平仓记录）。
- `positions.close_snapshot` 保存平仓时的 `exit_signal`、mark、用户选择的 `selected_close_reason` 与 `exit_signal_id`；旧持仓可为空。

## 2. Provider

- 接口与缓存策略：规格 §5。
- MVP：`provider_yfinance`；Tradier / IBKR 预留。

## 3. 策略与设置

- 过滤、评分、出场信号、`settings` JSON：规格 §7。

## 4. 测试与运维

- 测试金字塔与 YAGNI：规格 §9。

## 5. 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `TRADIER_API_KEY` | — | Tradier 沙盒/生产 API key（可选，`provider_tradier` 使用） |
| `TRADIER_BASE_URL` | `https://sandbox.tradier.com/v1` | Tradier 端点 |
| `SERVER_PORT` | `7000` | Flask server 端口 |
| `SKIP_INSTALL` | — | 设为 `1` 可跳过 pip install，加速重启 |

## 6. 关键实现说明

### Greeks 回退策略

`provider_yfinance` 返回的期权链可能缺少 Greeks（`delta=None` 等）。
`app/jobs/job_screener.py` 调用 `greeks.fill_greeks(contract, spot, rate)` 对每个合约进行回填；
Black-Scholes 所用无风险利率来自 `settings.risk_free_rate`（默认 4.5%）。

### IV Rank（RV 代理）

每日 `job_iv_history` 拉取 252 天对数收益率标准差年化值，存入 `settings.rv_by_symbol[symbol]` 列表。
筛选时以当前 ATM IV 在历史 RV 的百分位作为 IV Rank（0-100）。UI 标注「代理指标」。

### 免费行情质量分级

`app/core/data_quality.py` 在策略评分旁路评估数据可信度，不改写策略 `score`。候选 API 同时返回 flat 字段与 `data_quality` 嵌套对象；`quality_flags` 在 DB 中为 JSON 字符串，Repo/API 层返回数组。

- `A`：双边报价、Greeks 与 IV Rank 来源足够完整，可用于决策。
- `B`：存在可接受的数据回退或提示项，例如 BS 回填 Greeks、IV Rank 使用 RV 代理、成交量缺失；可展示和入场，但 UI 会提示。
- `C`：存在 blocker，例如 bid/ask 无效、价差过宽、Delta 缺失、OI/ROI/保证金缓冲不达标、到期窗口不合规；不写入 `candidates`，仅进入 `scan_runs.diagnostics`。
- `unknown`：旧数据或未带质量字段的行。

### 期权池与观察池

每轮 `job_screener` 完成候选写入后，会将本轮候选 upsert 到 `option_pool`；质量为 `C` 或带 blocker 的拒绝行可进入池并标记 `BLOCKED`。首次入池为 `NEW`，再次命中转 `ACTIVE`；未命中时增加 `missed_scan_count`，连续两轮未出现转 `STALE`；到期日早于今天转 `EXPIRED`。

观察池评估只处理 `WATCHING/READY` 项。过期优先转 `EXPIRED`；`IGNORED/OPENED/EXPIRED` 不自动唤醒；有用户目标时所有目标达成且合约可行动才转 `READY`，无目标时 `NEW/ACTIVE` 合约即可转 `READY`。READY、过期、BLOCKED/STALE 状态变化会写事件，并用 `last_signal` 去重。

API：

- `GET/PATCH /api/pool/underlyings/:symbol`、`POST /api/pool/underlyings/:symbol/pause|archive`
- `GET /api/pool/options`，支持 `symbol/status/quality_grade/min_score/min_dte/max_dte/entry_signal_status/min_entry_signal_score`
- `GET/POST/PATCH /api/watch/options`、`POST /api/watch/options/:id/ignore|open`

观察池开仓通过本地登记创建 `positions`，`open_snapshot` 写入 `option_pool_id`、`option_watchlist_id` 与池内质量/指标快照；仍不连接券商、不自动下单。

### 智能开仓信号

`app/core/entry_signal.py` 生成 `entry_signal_v1`，作为独立于策略 `score` 的解释层。状态含义：

- `OPENABLE`：收益、风险、流动性和数据质量达到人工确认门槛。
- `WAIT`：无硬性阻断，但权利金、收益、价差、用户观察目标或时机尚未达标。
- `REJECT`：存在硬性阻断，例如合约池 `BLOCKED`、质量 `C`、无效双边报价、DTE 不合规、安全垫不足。
- `EXPIRED`：合约已过期。
- `UNKNOWN`：数据不足，无法稳定判断。

每个信号包含收益、风险、流动性、波动、时机、数据质量六组指标，以及结构化 `reasons/blockers`。`job_screener` 每轮扫描后为本轮 upsert 的合约写入 `entry_signals`，并在 `scan_runs.diagnostics.totals.entry_signal_counts` 记录数量。观察池评估会参考最新开仓信号：`WAIT/REJECT/UNKNOWN` 不会自动转 `READY`；`OPENABLE` 且用户目标达成时才转 `READY`。开仓登记会把完整 `entry_signal` 写入 `positions.open_snapshot`，供后续复盘查看。

### 智能平仓与持仓动作建议

`app/core/exit_signal.py` 生成 `exit_signal_v1`，作为旧 `evaluate_exit_signals()` 的解释层。动作枚举：

- `HOLD`：继续持有/观察。
- `HOLD_TO_EXPIRY`：临期、剩余价值很低且安全垫仍满足条件，可考虑等待到期。
- `TAKE_PROFIT`：达到普通止盈阈值。
- `ACCELERATE_TAKE_PROFIT`：短时间快速获利，建议锁定。
- `TIME_EXIT`：临近到期，gamma/尾部风险上升。
- `DEFEND`：跌破行权价、安全垫为负、Delta 或浮亏触发防守。
- `EXPIRED`：持仓已过期但尚未结算。
- `UNKNOWN`：行情或 mark 数据不足。

默认新增设置位于 `settings.exits`：`fast_profit_days`、`fast_profit_pct`、`loss_pnl_pct_warn`、`loss_pnl_pct_danger`、`expiry_hold_max_mid`、`expiry_hold_min_margin_buffer`。`take_profit_fast` 与 `loss_breach` 是新增平仓原因；旧 `take_profit_50/75`、`time_7d/14d`、`danger_3pct`、`delta_breach` 继续兼容。

`job_radar` 每轮对 OPEN 持仓执行 mark → `build_exit_signal` → 写 `radar_snapshots` 与 `exit_signals`。`HOLD/UNKNOWN` 不发事件；`TAKE_PROFIT`、`ACCELERATE_TAKE_PROFIT`、`TIME_EXIT`、`DEFEND`、`HOLD_TO_EXPIRY`、`EXPIRED` 会按 `action + suggested_close_reason + severity` 去重后写 radar 事件。所有动作仍由用户在前端手动确认，不自动下单、不自动滚仓。

### 跨进程事件推送

1. Worker jobs 调用 `repo.insert_event(...)` 写入 SQLite
2. Worker 发 `POST 127.0.0.1:7000/api/internal/notify {"id": <event_id>}`
3. Server 查 DB 拿到事件，调用 `bus.publish(event)`
4. 所有订阅的 SSE 连接推送 `event: event` 数据行
5. Worker 推送失败只记录 debug log，事件仍在 DB，前端轮询 `/api/events` 可补拉。

### Settings 热加载

用户在 `#settings` 保存 → `POST /api/settings` → server 写 DB → `POST 127.0.0.1:7001/reload` → worker 重读 settings 并调用 `register_jobs(scheduler, repo)` 重新注册所有 job（含新时间间隔）。

### 复盘 API（Phase 5）

- `GET /api/review/summary?since=&until=&symbols=&pool=&min_sample=`：返回汇总、`slices`（九维条件切片）、`performance_review`、`score_pnl_correlation`；兼容字段 `factor_slices` 与 `setting_suggestions`。
- `GET /api/review/suggestions`：返回带 `changes[]` 的可应用建议列表。
- `POST /api/review/suggestions/apply`：body `{"suggestion_ids":[]}`，原子 merge settings；worker reload 失败时回滚并返回 409。
- Settings 新增：`entry_signal.openable_only`、`review.min_sample_size`、`review.score_correlation_buckets`。

## 7. 故障排查

| 症状 | 排查 |
|------|------|
| `curl 127.0.0.1:7001/healthz` 无响应 | worker 未启动，检查 `data/worker.pid` 与进程 |
| 浏览器 SSE 绿点不亮 | 检查 server 日志；刷新页面重连 |
| 候选表空 | 先在 #screener 保存观察名单，再点「立即扫描」 |
| `yfinance` 限流报错 | `requests-cache` 会自动缓存 5 分钟；等待后重试 |
