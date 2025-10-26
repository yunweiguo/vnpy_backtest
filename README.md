期权回测平台（vn.py 运行时）—— API 优先的最小骨架

本仓库提供与 docs/ 文档一致的最小可用实现，包含：

- FastAPI + Uvicorn 的 API（配置校验、回测生命周期、默认参数/预设）。
- 基于 RQ（Redis）的任务队列与 Worker 执行回测任务。
- 可配置的 MySQL 连接（股票库、期权行情库 US/HK、期权历史库）与 Redis/产物目录。
- DataProvider：支持按月分表的期权历史、股票日线读取；按市场时区计算会话日/DTE。
- 最小回测 Runner：演示 CSP / Short Put Vertical 的择券流程并写入产物。

环境与依赖

- Python ≥ 3.10（推荐 3.12，本仓库提供 `.venv` 示例）
- 安装依赖：`pip install -r requirements.txt`
- 配置文件：`config/settings.toml`
  - `[mysql]`：分别填写股票库 / 美股期权行情库 / 港股期权行情库 / 历史库的 DSN
  - `[redis]`：`url`
  - `[artifacts]`：`root`（产物根目录）
  - `[logging]`：日志级别、日志文件、是否输出到控制台（默认写入 `./logs/backtest.log`）
- 可选环境变量：
  - `BACKTEST_CONFIG_PATH`：自定义配置文件路径
  - `BACKTEST_<字段名大写>`：临时覆盖某个配置值（如 `BACKTEST_REDIS_URL`）

多环境配置

- 默认配置：`config/settings.toml`
- 示例模板：
  - 开发：`config/settings.dev.toml`
  - 测试：`config/settings.test.toml`
  - 生产：`config/settings.prod.toml`
- 运行时可通过 `BACKTEST_CONFIG_PATH=config/settings.test.toml` 切换；单项变量也可用 `BACKTEST_<字段名>` 覆盖。

运行

1. 创建并激活虚拟环境（示例）
   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. 启动 API：`uvicorn api.main:app --reload --port 8000`
3. 启动 Worker：`rq worker --url <redis连接串> backtest`

CLI

- 直接使用 CLI 与 API 交互 / 本地运行：
  ```bash
  python cli/backtest.py submit examples/config_csp_spv_us.json
  python cli/backtest.py status <run_id>
  python cli/backtest.py artifacts <run_id>
  python cli/backtest.py cancel <run_id>
  python cli/backtest.py local-run examples/config_csp_spv_us.json
  ```
- 如需自定义地址/鉴权，可设置：
  - `BACKTEST_API_BASE`（默认 `http://127.0.0.1:8000`）
  - `BACKTEST_API_KEY` / `BACKTEST_BEARER`
- `local-run` 会直接在本地执行回测，打印 Summary 与最近若干条交易；可使用 `--no-save` 跳过写入产物，`--trades N` 控制展示的交易数量。默认仍写入 `artifacts/<run_id>/` 以便复盘。

API 概览

- `GET /health`：健康检查
- `POST /strategies/validate`：配置校验与归一化（返回 normalized_config、fingerprint）
- `POST /backtests`：创建回测（支持 Idempotency-Key），返回 `run_id`
- `GET /backtests/{run_id}`：查询状态
- `GET /backtests/{run_id}/artifacts`：列出产物文件
- `POST /backtests/{run_id}/cancel`：取消回测
- `GET /diagnostics/option-chain`：诊断端点（见下）

诊断端点（链与连接）

- 路径：`GET /diagnostics/option-chain`
- 入参（Query）：
  - `market`：`US` 或 `HK`
  - `symbol`：期权 symbol，如 `AAPL` 或 `TCH.HK`
  - `session_date`：会话日（本地市场时区），格式 `YYYY-MM-DD`
  - `dte_min/dte_max`：目标 DTE 窗口，默认 `30/60`
  - `delta_lo/delta_hi`：短腿 |Δ| 区间（CSP/SPV），默认 `0.18/0.25`
  - `min_oi/min_volume/max_spread_pct`：流动性门槛，默认 `500/100/0.08`
  - `top_k`：候选返回条数，默认 `3`
- 返回：
  - `connectivity`：四路数据库连接状态（股票实例/美股期权行情实例/港股期权行情实例/历史实例）
  - `chain_raw`：原始链统计（总数、P/C 数、到期集合、DTE 范围、IV/Δ 缺失数）
  - `chain_filtered`：按照 Δ 与流动性筛选后的统计
 - `csp_top`/`spv_top`：CSP/SPV 的 Top‑K 候选摘要

产物与示例

- 产物目录：`artifacts/{run_id}/`，含 `decisions.jsonl`、`trades.csv`、`chain.json`、`metrics.json`
- 示例配置：`examples/config_csp_spv_us.json`
- 进度追踪：`docs/STATUS.md`
- 日志/诊断说明：`docs/LOGS_DIAGNOSTICS.md`
- 日志配置：在 `config/settings*.toml` 的 `[logging]` 段配置 `level/file/console`；日志位于 `logs/` 目录（默认 RollingFile + 控制台）。
- API 验证：`docs/API_TESTING.md`（Postman 与 requests 脚本）

回归脚本

- 本地 Smoke：
  ```bash
  python scripts/run_smoke.py --config examples/config_csp_spv_us.json
  ```
  该脚本会加载配置、运行一次回测，并校验是否产生交易/决策，适合在 CI 或定期回归时使用。
- API Smoke：
  ```bash
  python scripts/run_api_smoke.py --config examples/config_csp_spv_us.json --base http://127.0.0.1:8000
  ```
  需要先启动 API 与 Worker。脚本会依次调用 health/validate/backtests/status/artifacts，超时或失败会返回非零退出码。

说明

- M1 聚焦 CSP 与 Short Put Vertical 的择券逻辑，展期/退出与完整 KPI 将按路线图逐步补齐。
- 港股乘数/最小跳从 `hkoption_quote.option_basic` 读取；美股乘数默认 100。
- 历史表采用“本地 00:00 → UTC 毫秒”的 `timestamp/expire_date`，运行时按市场时区恢复为会话日/到期日并计算 DTE。
