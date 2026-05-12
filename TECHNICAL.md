# TECHNICAL.md — 数据与策略实现细节

> 详细定义见 **[设计规格 §4、§5、§7](docs/superpowers/specs/2026-05-12-us-option-trading-assistant-design.md)**。

## 1. 数据库

- 路径：`data/options.db`（WAL）；建表语句见规格 §4.3。
- 快照：`data/snapshots/*.ndjson`（不入版本库）。

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

### 跨进程事件推送

1. Worker jobs 调用 `repo.insert_event(...)` 写入 SQLite
2. Worker 发 `POST 127.0.0.1:7000/api/internal/notify {"id": <event_id>}`
3. Server 查 DB 拿到事件，调用 `bus.publish(event)`
4. 所有订阅的 SSE 连接推送 `event: event` 数据行
5. Worker 推送失败只记录 debug log，事件仍在 DB，前端轮询 `/api/events` 可补拉。

### Settings 热加载

用户在 `#settings` 保存 → `POST /api/settings` → server 写 DB → `POST 127.0.0.1:7001/reload` → worker 重读 settings 并调用 `register_jobs(scheduler, repo)` 重新注册所有 job（含新时间间隔）。

## 7. 故障排查

| 症状 | 排查 |
|------|------|
| `curl 127.0.0.1:7001/healthz` 无响应 | worker 未启动，检查 `data/worker.pid` 与进程 |
| 浏览器 SSE 绿点不亮 | 检查 server 日志；刷新页面重连 |
| 候选表空 | 先在 #screener 保存观察名单，再点「立即扫描」 |
| `yfinance` 限流报错 | `requests-cache` 会自动缓存 5 分钟；等待后重试 |
