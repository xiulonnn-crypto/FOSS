# us-option-trading-assistant

本地期权卖方助手（MVP：Cash-Secured Short Put）：筛选、雷达、到期结算、复盘；人在回路，不自动下单。

## 权威规格

设计与范围以 **[设计规格](docs/superpowers/specs/2026-05-12-us-option-trading-assistant-design.md)** 为准。

## 快速开始

```bash
python3 run.py          # 安装依赖 → 初始化 DB → 起 worker → 起 server → 自动开浏览器
SKIP_INSTALL=1 python3 run.py  # 已安装时跳过 pip，加速启动
```

macOS 可双击 `启动期权助手.command`。

| 进程 | 访问地址 |
|------|---------|
| server（主 UI & API） | http://127.0.0.1:7000 |
| worker（内部管理） | http://127.0.0.1:7001 |

## API 端点速览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/settings` | 读取配置 |
| POST | `/api/settings` | 更新配置（自动触发 worker 热加载） |
| GET/POST | `/api/watchlist` | 观察名单 |
| POST | `/api/scan/run` | 手动触发筛选 |
| GET | `/api/scan/latest` | 最新候选合约 |
| GET/POST | `/api/positions` | 持仓列表 / 新建 |
| POST | `/api/positions/{id}/close` | 平仓 |
| GET | `/api/positions/{id}/radar` | 雷达历史 |
| GET | `/api/events` | 事件列表 |
| PUT | `/api/events/{id}/ack` | 已读单条事件 |
| GET | `/api/events/stream` | SSE 实时推送 |
| GET | `/api/review/summary` | 复盘四指标 + 出场原因分组 |
| GET | `/api/review/positions.csv` | 导出持仓 CSV |

## 测试

```bash
python3 -m pytest tests/ -v
```

## 文档五件套

| 文件 | 说明 |
|------|------|
| [CLAUDE.md](CLAUDE.md) | AI / 协作者入口 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 系统架构（引用规格第 3、6 节） |
| [DESIGN.md](DESIGN.md) | UI / 前端信息架构（引用规格第 8 节） |
| [TECHNICAL.md](TECHNICAL.md) | 数据与策略细节（引用规格第 4、5、7 节） |
