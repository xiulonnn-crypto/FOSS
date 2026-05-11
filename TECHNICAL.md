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

（实施阶段在此补充 API 列表、环境变量、故障排查。）
