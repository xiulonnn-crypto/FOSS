# us-option-trading-assistant

本地期权卖方助手（MVP：Cash-Secured Short Put）：筛选、雷达、到期结算、复盘；人在回路，不自动下单。

## 权威规格

设计与范围以 **[设计规格](docs/superpowers/specs/2026-05-12-us-option-trading-assistant-design.md)** 为准。

## 运行（实施阶段提供）

- Web：`http://127.0.0.1:7000`（Flask `server.py`）
- Worker：内部 `http://127.0.0.1:7001`（`/reload`、`/healthz`）
- 一键：`python3 run.py`（待实现）

## 文档五件套

| 文件 | 说明 |
|------|------|
| [CLAUDE.md](CLAUDE.md) | AI / 协作者入口 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 系统架构（引用规格第 3、6 节） |
| [DESIGN.md](DESIGN.md) | UI / 前端信息架构（引用规格第 8 节） |
| [TECHNICAL.md](TECHNICAL.md) | 数据与策略细节（引用规格第 4、5、7 节） |
