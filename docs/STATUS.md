# 项目进度追踪（滚动更新）

> 版本：v0.1（初始化草稿）

## 里程碑概览

| 里程碑 | 目标 | 时间窗 | 当前状态 | 备注 |
| --- | --- | --- | --- | --- |
| M1 | API + 最小 Worker（CSP/SPV）可跑通 | 2025-Q1 | 进行中 | FastAPI/RQ 骨架、DataProvider 初版已完成 |
| M2 | 展期/保护翼链路、组合成交、决策时间线 | 2025-Q2 | 未开始 | 依赖数据质量验证与成交模型实现 |
| M3 | 回测=模拟一致性、异常恢复、观测面板 | 2025-Q3 | 未开始 | 需要日志口径稳定后推进 |
| M4 | 参数预设面板、报表导出、CI/调度接入 | 2025-Q4 | 未开始 | 与前端/平台集成同步 |

## Sprint / 周期更新

### 2025-02-XX ~ 2025-02-XX
- ✅ 完成：
  - FastAPI API 端点、配置校验、回测队列骨架
  - MySQL DataProvider（分环境 DSN、期权链诊断端点）
  - CSP / Short Put Vertical 最小择券逻辑与产物落地
  - vn.py BacktestingEngine 适配（OptionRollStrategy/`vnpy-run` CLI）
  - CSP/SPV 退出与展期链路：基于短腿 Δ/DTE 的 manage window、滚动候选筛选与链路记录（ENTRY/ROLL/EXIT）
  - 填充产物指标（年化收益、Sharpe、回撤比例等），并输出 Fill Guard/执行拒单计数
  - 保护翼、Fill Guard 与组合成交逻辑：支持 wing_policy、执行价格偏好、拒单补偿，示例配置可直接触发
- ⏳ 进行中：
  - 数据链诊断深化（IV/Δ 缺失率、放宽日志）
  - 引入 option_stat、IVR 过滤与更多策略模板
- ⚠️ 风险/阻碍：
  - pip 官方包依赖 `typing.TYPE_CHECKING` 为 True 的兼容性问题（已通过 `.venv` patch 解决）
  - 生产数据源的权限与覆盖范围待确认

## 待办与优先级

1. 引入 option_stat 的 ATM IV 序列，完善 IVR 过滤（中高）
2. 数据诊断/日志强化：IV/Δ 缺失率统计、放宽流程追踪（中高）
3. 多策略配置示例与 Postman 集合同步（中）
4. 规划 CI 流程：单元测试 + 集成回测样例（低）
5. 保护翼/执行逻辑的更多策略（如铁鹰/蝶式）扩展（低）

> 更新流程：建议每个 Sprint 结束后刷新上述表格与日志，保留历史段落，便于审计与复盘。
