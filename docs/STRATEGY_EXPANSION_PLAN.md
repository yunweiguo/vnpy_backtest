# 策略扩展规划（多策略支持）

> 状态：2025-02 草案，覆盖从“仅 CSP/SPV”到“多模板策略”落地的主要工作。后续每个阶段完成后需回写本文件与 `docs/STATUS.md`。

## 背景

- 目前运行时（`core/strategy/runtime.py`）和 API 仅绑定 `csp_spv_us`，通过 `selector_csp_spv.py` 提供的 `csp_candidates/spv_candidates` 来完成择券与展期。
- 业务希望在 2025 年上半年引入更多卖方策略（如 Short Call Vertical、Iron Condor、Cash-Secured Strangle 等），并能在配置中一键切换。
- 目标是让策略抽象、配置、诊断、测试与文档都支持“多策略”这一主线，而不是在各处硬编码。

## 总体目标

1. **策略插件化**：以 `StrategySpec` 接口抽象 entry/roll/exit 钩子，允许后续策略（含多腿/标的腿）快速接入。
2. **配置归一**：`ConfigModel`、API `/reference`、CLI 示例与文档可以声明不同策略及其特定参数，同时维持兼容已有 `csp_spv_us`。
3. **验证手段**：新增策略需要配套 selector 单测、回测回归样例以及 API smoke，确保多模板同时存在时结果可复现。

## 阶段拆解

### 阶段 1 – 策略清单与需求冻结
- 与业务确认首批要支持的策略（建议 Short Call Vertical、Iron Condor、Cash-Secured Strangle）。
- 定义每种策略的核心参数：短腿 Delta、腿宽、信用阈值、是否需要标的多头或现金担保等。
- 明确风控指标（TP/SL、Delta 管控、保证金/权益要求）以及回测 KPI 输出项。

### 阶段 2 – 策略接口/注册表
- 已落地：`core/strategy/registry.py` 提供 `SelectorSpec/registry`，`core/strategy/selectors/` 下拆分 puts/calls/condor 选择器并在 `__init__.py` 注册 `CSP`（Cash-Secured Put）、`SPV`（Short Put Vertical）、`SCV`（Short Call Vertical）、`LCV`（Long Call Vertical）、`IC`（Iron Condor），`StrategyRuntime` 通过注册表读取，旧逻辑保留为兜底。
- 待完善：整理 `StrategySpec`/滚动规则接口，剥离 roll/wing 等逻辑为可插拔组件；positions 侧需引入多腿模板（标的腿/数量）。

### 阶段 3 – 配置 & API
- 扩展 `ConfigModel` 与 `normalize_config`，为新的策略引入特定字段（例如 call delta、put/call 独立宽度、目标信用比等）。
- `/reference/resolve-profile` 返回不同 `preset` 的策略模板；`/diagnostics/option-chain` 支持指定策略类型并输出对应候选。
- CLI/示例配置目录下新增多策略样例，并在配置校验/错误提示中指出策略上下文。

### 阶段 4 – 具体策略实现
- 复制/拆分 `selector_csp_spv.py`，为 Short Call Vertical、Iron Condor、Cash-Secured Strangle 各自实现候选生成逻辑。
- Iron Condor 需要同时选出 put/call 组合，Cash-Secured Strangle 要处理股票腿或保证金占用；必要时扩充 Provider 接口以读取标的行情。
- 输出统一的 `Candidate` 结构，供 runtime/执行层消费。

### 阶段 5 – 回测执行扩展
- 依据新策略的 manage window/roll 规则，补充 `_handle_entry/_handle_roll/_maybe_add_wing` 等逻辑。
- 让 Fill Guard、execution 配置支持策略级 override；展期失败时的 fallback 行为需按策略自定义。
- 更新 `worker/backtest_core.py` 与 `vnpy_adapter`，确保本地/分布式回测都可运行多策略任务。

### 阶段 6 – 测试矩阵
- Selector 层编写 Mock Provider 单测（覆盖 Delta/宽度边界、候选排序、过滤条件）。
- 回测层新增集成样例：至少 1 个 symbol × 每种策略 × 指定时间窗，纳入 `scripts/run_smoke.py` 及 API smoke。
- 若策略依赖股票腿/持仓，需添加 PnL 回放脚本和保证金核对检查。

### 阶段 7 – 文档与交付
- 更新 `README.md`、`docs/LOGS_DIAGNOSTICS.md`、`examples/`，记录策略清单、参数说明、适用市场。
- 在 `docs/STATUS.md` / Roadmap 中同步进度；补充发布说明与兼容性提醒。
- 真实数据回测验证，并输出示例指标/交易截图，供业务评审。

## 依赖与风险

| 项 | 描述 | 缓解措施 |
| --- | --- | --- |
| 数据质量 | 新策略对 IV/Δ/Spread 精度要求更高 | 先扩展诊断脚本，补齐缺失统计与报警 |
| runtime 重构 | 插件化期间需确保 CSP/SPV 行为不回归 | 引入特性开关，先通过 A/B 配置运行，配套回归脚本 |
| 交割/保证金 | Strangle/Condor 涉及多腿净额与保证金计算 | 与风控确认模型，必要时接驳模拟保证金 API |

## 下一步（短期行动）

1. 在本周内完成策略清单/参数需求确认，产出 PRD 补充条目。
2. 启动 registry 设计（包含接口草图、对 runtime 的影响评估）。
3. 为 Short Call Vertical 起草 selector 草图，并准备示例配置（用于后续 smoke）。

> 以上计划与 `docs/Roadmap_WBS_Full_v3.0_API_vnpy.md` 的阶段划分保持一致，后续如需调整请在 PR 中同步更新两个文件。
