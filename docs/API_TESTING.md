# API 验证与回归

## 1. Postman 集合
- 集合路径：`docs/postman_collection_v1.json`
- 推荐流程：
  1. 启动 API（`uvicorn api.main:app --reload`）与 Worker（`rq worker --url ... backtest`）。
  2. 在 Postman 导入集合，配置 `baseUrl`、`apiKey`、`bearer` 等变量。
  3. 顺序调用：Health → Validate → Create Backtest → Get Status → Artifacts。

## 2. 自动化 Smoke（requests）
脚本：`scripts/run_api_smoke.py`

```bash
python scripts/run_api_smoke.py --config examples/config_csp_spv_us.json \
       --base http://127.0.0.1:8000
```

环境变量：`BACKTEST_API_KEY`、`BACKTEST_BEARER`、`BACKTEST_API_BASE` 可自动带入。

脚本流程：
1. `GET /health`
2. `POST /strategies/validate`
3. `POST /backtests`
4. 轮询 `GET /backtests/{run_id}` 直到 COMPLETED/FAILED/CANCELED 或超时
5. `GET /backtests/{run_id}/artifacts`

如需在 CI 中使用，可先启动 API/Worker，再执行该脚本，并根据退出码判断成功与否。

## 3. 其他说明
- 可通过 `--timeout`、`--poll-interval` 调整轮询策略。
- Postman 与脚本可以结合使用：先用 Postman 验收接口/鉴权，再在 CI 中跑脚本做回归。
- 若需下载产物或进一步校验，可在脚本基础上拓展。
