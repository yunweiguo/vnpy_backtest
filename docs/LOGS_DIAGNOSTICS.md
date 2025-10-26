# 日志与诊断说明

## 1. 决策与交易日志

- `decisions.jsonl`
  - 每行一个 JSON，记录当日/当次行动。
  - 关键字段：
    - `date`：决策日期（字符串）
    - `action`：`ENTRY / EXIT / ROLL_ENTRY / ROLL_EXIT / SKIP` 等
    - `position_id`、`chain_id`：所属持仓与链
    - `metrics`：该动作的关键指标，如 `entry_credit`、`pnl`、`profit_pct`
    - `info`：入场/滚仓候选的详细信息
    - `reason`：退出或跳过的原因（例如 `hard_exit_dte`、`manage_window_no_roll`、`missing_quotes`）

- `trades.csv`
  - 结构化的成交/动作记录，包含净收支 (`net_credit`) 与 `pnl`。
  - `details_json` 提供额外上下文，例如腿的快照、候选信息、退出原因。

- `chain.json`
  - 每条链的时间线。`events` 会按顺序列出 `ENTRY → ... → EXIT`，便于复盘。

- `metrics.json`
  - 汇总指标：总收益、最大回撤、赢率、平均单笔收益/亏损、起止日期、运行的标的数量等。

- `charts/`
  - `equity_drawdown.png`：资金曲线 & 回撤
  - `pnl_distribution.png`：单笔收益分布直方图

## 1.1 运行日志

- 日志配置由 `settings.toml` 的 `[logging]` 控制，默认写入 `./logs/backtest.log`，并同时输出控制台。
- 关键日志：
  - `worker.backtest_core`：回测开始/结束、进出场、滚仓、缺失行情等事件
  - `worker.runner`：队列任务状态
  - `api`：配置校验、任务创建
- 日志格式：`YYYY-MM-DD HH:MM:SS [LEVEL] logger-name - message`，附加 `extra` 信息可用于排查（如 `run_id`、`position_id`、`contract_id`）。

## 2. 诊断接口 `/diagnostics/option-chain`

返回字段示例：
- `connectivity`：股票库、期权行情库（US/HK）、历史库连接状态。
- `chain_raw` 与 `chain_filtered`：当日链的数量、到期分布、IV/Δ 缺失情况。
- `csp_top` / `spv_top`：筛选后的 Top-K 候选（含评分、腿信息、信用/宽度、流动性指标）。
- `params`：本次诊断使用的参数（DTE、Δ、流动性门槛等）。

使用建议：
1. 回测前先调用诊断接口，确认当日链的质量与候选数量。
2. 通过 `missing_iv`/`missing_delta` 评估数据覆盖率；若缺失率高，考虑放宽参数或检查数据源。
3. 若 `chain_filtered.total` 为 0，可查看 `chain_raw` 与 `params` 是否过于严格。

## 3. CLI 诊断

CLI `local-run` 默认打印：
- Summary：`entries/exits/rolls/net_pnl/max_drawdown/win_rate` 等
- 最近 N 条交易：日期、动作、结构、净入账、PnL
- 产物与图表位置

支持参数：
- `--trades N`：展示最近 N 条交易（默认 10）
- `--no-save`：仅打印，不写产物（用于快速诊断）

## 4. 常见问题定位

- **策略长时间无交易**：
  - 检查 `decisions.jsonl` 中是否大量 `SKIP missing_quotes` → 考虑放宽流动性或修复行情。
  - 诊断接口查看 `chain_filtered.total` 是否为 0。
- **收益异常**：
  - `metrics.json` 与资金曲线图可帮助定位亏损阶段。
  - 结合 `trades.csv` 的 `details_json` 查看当次退出原因/腿快照。
- **滚仓未触发**：
  - `chain.json` 中若没有 `ROLL_EXIT/ROLL_ENTRY`，检查 `manage_at_dte_lte` 与候选 Delta/宽度是否过严。
