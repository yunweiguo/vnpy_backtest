#!/usr/bin/env python3
"""API smoke test: calls health, validate, create backtest, poll status, list artifacts.

Usage:
    python scripts/run_api_smoke.py --config examples/config_csp_spv_us.json \
        --base http://127.0.0.1:8000

Environment variables (optional):
    BACKTEST_API_KEY, BACKTEST_BEARER  -> add auth headers
    BACKTEST_API_BASE                 -> default base URL

Ensure API 服务和 Worker 已启动后再执行。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict

import requests


def headers() -> Dict[str, str]:
    hdrs = {"Content-Type": "application/json"}
    api_key = os.getenv("BACKTEST_API_KEY")
    bearer = os.getenv("BACKTEST_BEARER")
    if api_key:
        hdrs["X-API-Key"] = api_key
    if bearer:
        hdrs["Authorization"] = f"Bearer {bearer}"
    return hdrs


def main() -> None:
    parser = argparse.ArgumentParser(description="Options Backtest API smoke test")
    parser.add_argument("--config", required=True, help="配置文件 (JSON)")
    parser.add_argument("--base", default=os.getenv("BACKTEST_API_BASE", "http://127.0.0.1:8000"), help="API 基础地址")
    parser.add_argument("--poll-interval", type=float, default=3.0, help="状态轮询间隔秒数")
    parser.add_argument("--timeout", type=int, default=300, help="最大等待时间 (秒)")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"配置文件不存在：{config_path}", file=sys.stderr)
        sys.exit(1)

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    hdrs = headers()

    # Health
    resp = requests.get(f"{base}/health", headers=hdrs, timeout=10)
    resp.raise_for_status()
    print("[SMOKE][API] health:", resp.json())

    # Validate
    resp = requests.post(f"{base}/strategies/validate", headers=hdrs, json=cfg, timeout=30)
    resp.raise_for_status()
    validation = resp.json()
    print("[SMOKE][API] validate fingerprint:", validation.get("fingerprint"))

    # Submit backtest
    resp = requests.post(f"{base}/backtests", headers=hdrs, json=cfg, timeout=30)
    resp.raise_for_status()
    run_id = resp.json()["run_id"]
    print("[SMOKE][API] backtest submitted run_id=", run_id)

    # Poll status
    start = time.time()
    status_url = f"{base}/backtests/{run_id}"
    while True:
        resp = requests.get(status_url, headers=hdrs, timeout=15)
        if resp.status_code == 404:
            print("[SMOKE][API] status not found yet, retrying...")
        else:
            resp.raise_for_status()
            status_payload = resp.json()
            status = status_payload.get("status")
            print("[SMOKE][API] status:", status_payload)
            if status in {"COMPLETED", "FAILED", "CANCELED"}:
                break
        if time.time() - start > args.timeout:
            print("[SMOKE][API] 超时仍未完成", file=sys.stderr)
            sys.exit(2)
        time.sleep(args.poll_interval)

    if status != "COMPLETED":
        print("[SMOKE][API] 回测未成功", file=sys.stderr)
        sys.exit(3)

    # List artifacts
    resp = requests.get(f"{base}/backtests/{run_id}/artifacts", headers=hdrs, timeout=15)
    resp.raise_for_status()
    artifacts = resp.json().get("artifacts", [])
    print("[SMOKE][API] artifacts:", artifacts)
    if not artifacts:
        print("[SMOKE][API] 无产物返回", file=sys.stderr)
        sys.exit(4)

    print("[SMOKE][API] 成功，run_id=", run_id)


if __name__ == "__main__":
    main()

