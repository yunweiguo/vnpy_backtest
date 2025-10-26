
# 研发实施规范 RD Spec —— 完整版 v3.0（API-first + vn.py）
> 日期：2025-10-25  
> 关联：OpenAPI v1 / PRD v3.0 / Roadmap v3.0

---

## 1. 架构
```
[API Gateway]
  ├─ OpenAPI 校验 / 幂等键 / 鉴权（Bearer + API Key）
  ├─ 入参归一化（profiles / overrides / 吸附与放宽建议）
  └─ 任务派发（队列/调度）
[Worker (vn.py)]
  ├─ StrategyCore
  │   ├─ PositionMonitor     # DTE/PnL/Greeks/IVR/事件/流动性
  │   ├─ Selector            # 入场/展期候选 + 打分回退
  │   ├─ ExitRollDecider     # 硬退/加翼/展期 决策树
  │   ├─ WingManager         # 保护翼生成与成本约束
  │   └─ Executor            # 组合净价/撮合/重试/补偿/幂等
  ├─ Journal & Snapshot      # 决策日志、订单流水、快照与回放
  └─ Artifacts               # trades.csv / chain.json / metrics.json / decisions.jsonl
```

---

## 2. 数据模型（核心）
### 2.1 结构
- `Leg`：symbol/expiry/strike/right/side/ratio；  
- `PositionChain`：一笔策略从入场到退出/到期/指派的一条链，含多次 roll/wing；  
- `Candidate`：择券/展期候选（含打分与拒绝原因）；  
- `Snapshot`：时点行情/Greeks/IVR/事件状态/持仓/保证金；  
- `OrderJournal`：下单与成交的全量记录；  
- `Decision`：一次入/展/翼/退的决策输入/判定/结论。

### 2.2 统一单位与口径
- 价格：以货币单位；点差与滑点按比例记录；  
- Greeks：BSM 或供应商；时间步与交易日历一致；  
- 费用：佣金/交易费/行权指派费逐腿计提；  
- 保证金/最大亏损：按结构即时重算。

---

## 3. 状态机
### 3.1 策略级
`INIT → FLAT → SCANNING → ENTRY_PENDING → IN_POSITION → MANAGE_WINDOW → ROLL_EVAL → (HEDGE|ROLL|EXIT)_PENDING → COOLDOWN → FLAT`  
异常：`PAUSED/ERROR/STOPPED`。

### 3.2 链级
`OPEN/ACTIVE/HEDGED/ROLLING/PARTIAL_CLOSE/CLOSED/EXPIRED/ASSIGNED`。

### 3.3 订单级
`NEW/WORKING/PARTIAL/FILLED/CANCELED/REPLACED/EXERCISED/ASSIGNED/EXPIRED`。

---

## 4. 事件循环（vn.py）
```python
class OptionRollStrategy(CtaTemplate):
    parameters = ["config_json"]
    variables = ["state", "chain_id"]

    def on_init(self):
        self.cfg = validate_and_freeze(self.config_json)         # pydantic 校验 + 预设/覆盖 + 吸附/放宽
        self.monitor = PositionMonitor(self.cfg)
        self.selector = SelectorImpl(self.cfg)
        self.decision = ExitRollDecider(self.cfg)
        self.wing = WingManager(self.cfg)
        self.exec = ExecutorAdapter(self.cfg, self)              # 与 vn.py 网关对接
        self.state_m = StateMachine()
        self.timer_spec = {"every": "60s", "align": "session"}

    def on_start(self):
        self.subscribe(self.cfg.strategy.symbols or [])
        self.register_timer(self.timer_spec)

    def on_bar(self, bar):
        snap = self.monitor.update(bar, self.now())
        if self.decision.hard_exit(snap):
            self.exec.close_all(); self.chain.end(); return
        if self.decision.manage_trigger(snap):
            if self.wing.should_add(snap): self.wing.add(bar); self.monitor.recalc()
            cands = self.selector.roll_candidates(snap)
            if cands and self.decision.roll_ok(cands): self.exec.roll_to(best(cands)); self.chain.record_roll()
            else: self.exec.partial_or_close(); self.chain.end()
        elif not self.chain.in_position():
            cands = self.selector.entry_candidates(snap)
            if cands: self.exec.open(best(cands)); self.chain.start()
```

---

## 5. 择券器（Entry/Roll）
- 搜索空间：DTE、执行价（按Δ/档位/中心）、结构（宽度/翼）、事件窗、流动性。  
- 打分：  
  - 信用：`score = w1*AER - w2*pen_spread - w3*pen_delta + w4*(credit/width) + w5*liquidity`  
  - 借记：`score = w1*EV - w2*pen_spread + w3*trend - w4*iv_penalty`  
- 回退：吸附→阶梯放宽（≤3档）→替代结构→放弃；全过程记录拒绝原因。

---

## 6. 展期与保护翼
- 前置条件（全部满足才滚）：降险有效（Δ/MaxLoss/保证金下降）+ 收益达标（年化/EV）+ 可成交（点差/OI/量）+ 结构一致（宽度/中心）+ 事件通过；  
- 保护翼：翼Δ 0.05–0.10，同到期优先；成本 ≤ 已收信用 20–40%，加翼后 `credit/width ≥ 1/3`。

---

## 7. 执行与撮合
- 组合净价优先；失败→拆腿 + 临时对冲 + 回滚；  
- 价格：`mid ± bps` 或 `spread×系数`；  
- FillGuard：`max_spread_pct / min_oi / min_volume`；  
- 重试：`max_attempts/backoff_sec`；  
- 幂等：`decision_id` + `idempotency_key = hash(run_id, chain_id, legs, action, ts_bucket)`；  
- 提前指派与到期：按 ITM、除息、外在价值建模概率，生成 `ASSIGNED/EXERCISED` 事件。

---

## 8. 费用与保证金
- 佣金/交易费按腿计提；  
- 行权/指派费在事件触发时计提；  
- 保证金：按结构（如信用价差宽度-已收信用）与券商规则近似建模，滚动重算。

---

## 9. 配置 Schema（与 OpenAPI 对齐）
- 顶层键：strategy/legs/selector/roll_policy/exit_policy/wing_policy/execution/fees/slippage/entry/monitor/tolerances/relaxation/profiles/user_overrides；  
- 统一使用 `legs[]` 表示单腿/多腿；`contract` 可在 API 层自动归一到 `legs[]`；  
- 完整字段解释见 OpenAPI（`components.schemas`）。

---

## 10. 日志与产物
- 决策日志（JSONL）：输入快照、命中阈值、放宽/拒绝原因、选择与备选列表；  
- 订单流水：提交→替换→成交→撤单→回滚；  
- 产物：`trades.csv`、`chain.json`、`metrics.json`、`decisions.jsonl`、`charts/*.png`；  
- 指标：错失率、组合单成功率、放宽触发率、加翼成本占比、回撤恢复期等。

---

## 11. 测试与一致性
- 单元：Selector 打分、Roll 前置、翼成本、指派判定、区间解析、点号覆盖；  
- 集成：极端行情/流动性干涸/除息前 ITM/无可滚合约；  
- 回归：ETF/热门股/长尾股 × 3 年窗口；  
- 一致性：同一数据 → 回测与模拟偏差 ≤ X%；记录偏差来源与修正。

---

## 12. 性能与可扩展
- 并发回测任务、参数网格/贝叶斯寻优、缓存与断点续跑；  
- 数据向量化与多进程/分布式（任务队列）；  
- 大文件产物分片与生命周期管理。

---

## 13. 错误码（与 API 对齐，节选）
- `VALIDATION_ERROR`（400）：字段越界/类型错误；  
- `RESOURCE_NOT_FOUND`（404）：run_id 不存在；  
- `RATE_LIMITED`（429）：超配额；  
- `CONFLICT`（409）：Idempotency-Key 冲突；  
- `ENGINE_FAILURE`（500）：Worker 异常（携带决策/订单上下文）。
