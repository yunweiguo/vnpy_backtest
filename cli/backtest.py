#!/usr/bin/env python3
"""命令行工具：方便以 CLI 方式与回测 API 交互。

示例：
  python cli/backtest.py submit examples/config_csp_spv_us.json
  python cli/backtest.py status <run_id>
  python cli/backtest.py artifacts <run_id>
  python cli/backtest.py cancel <run_id>
  python cli/backtest.py local-run examples/config_csp_spv_us.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.main import ConfigModel, normalize_config
from settings import load_settings
from core.logging_config import configure_logging
from worker.backtest_core import execute_backtest, save_artifacts
from vnpy_adapter.runner import run_vnpy_backtest


def _base_url() -> str:
    return os.getenv("BACKTEST_API_BASE", "http://127.0.0.1:8000")


def _headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("BACKTEST_API_KEY")
    bearer = os.getenv("BACKTEST_BEARER")
    if api_key:
        headers["X-API-Key"] = api_key
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return headers


def cmd_submit(args: argparse.Namespace) -> None:
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"配置文件不存在：{config_path}", file=sys.stderr)
        sys.exit(1)
    body = json.loads(config_path.read_text(encoding="utf-8"))
    url = f"{_base_url()}/backtests"
    headers = _headers()
    if args.idempotency_key:
        headers["Idempotency-Key"] = args.idempotency_key
    resp = requests.post(url, headers=headers, json=body, timeout=30)
    _print_response(resp)


def cmd_status(args: argparse.Namespace) -> None:
    url = f"{_base_url()}/backtests/{args.run_id}"
    resp = requests.get(url, headers=_headers(), timeout=15)
    _print_response(resp)


def cmd_artifacts(args: argparse.Namespace) -> None:
    url = f"{_base_url()}/backtests/{args.run_id}/artifacts"
    resp = requests.get(url, headers=_headers(), timeout=15)
    _print_response(resp)


def cmd_cancel(args: argparse.Namespace) -> None:
    url = f"{_base_url()}/backtests/{args.run_id}/cancel"
    resp = requests.post(url, headers=_headers(), timeout=15)
    _print_response(resp)


def cmd_validate(args: argparse.Namespace) -> None:
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"配置文件不存在：{config_path}", file=sys.stderr)
        sys.exit(1)
    body = json.loads(config_path.read_text(encoding="utf-8"))
    url = f"{_base_url()}/strategies/validate"
    resp = requests.post(url, headers=_headers(), json=body, timeout=30)
    _print_response(resp)


def cmd_defaults(_: argparse.Namespace) -> None:
    url = f"{_base_url()}/reference/defaults"
    resp = requests.get(url, headers=_headers(), timeout=15)
    _print_response(resp)


def cmd_local_run(args: argparse.Namespace) -> None:
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"配置文件不存在：{config_path}", file=sys.stderr)
        sys.exit(1)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        cfg = ConfigModel.model_validate(raw)
    except Exception as exc:
        print(f"配置解析失败：{exc}", file=sys.stderr)
        sys.exit(1)

    normalized = normalize_config(cfg)
    run_id = args.run_id or str(uuid.uuid4())

    try:
        settings = load_settings()
        configure_logging(settings.log_level, settings.log_file, settings.log_console)
        result = execute_backtest(normalized, settings)
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        raise

    artifacts_root = settings.artifacts_root
    run_dir = Path(artifacts_root) / run_id

    chart_paths: List[Path] = []
    if not args.no_save:
        chart_paths = save_artifacts(run_id, result, artifacts_root)
        artifacts_msg = str(run_dir)
    else:
        artifacts_msg = None

    summary = result.get("summary", {})
    trades = result.get("trades", [])

    print("=== Summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    show_count = args.trades if args.trades is not None else 10
    if trades:
        subset = trades[-show_count:] if show_count > 0 else trades
        print(f"\n=== Trades (latest {len(subset)} of {len(trades)}) ===")
        for trade in subset:
            net = "-" if trade["net_credit"] is None else f"{trade['net_credit']:.2f}"
            pnl = "-" if trade["pnl"] is None else f"{trade['pnl']:.2f}"
            print(f"{trade['date']} {trade['symbol']} {trade['action']} {trade['kind']} net={net} pnl={pnl}")
    else:
        print("\n=== Trades ===\n(无交易记录)")

    print("\n=== Output ===")
    print(f"run_id: {run_id}")
    if artifacts_msg:
        print(f"已写入产物目录: {artifacts_msg}")
        if chart_paths:
            print("图表:")
            for path in chart_paths:
                print(f"  - {path}")
    else:
        print("未写入产物 (--no-save 模式)")
    print(f"Metrics JSON: {run_dir / 'metrics.json'}")


def cmd_vnpy_run(args: argparse.Namespace) -> None:
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"配置文件不存在：{config_path}", file=sys.stderr)
        sys.exit(1)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        cfg = ConfigModel.model_validate(raw)
    except Exception as exc:
        print(f"配置解析失败：{exc}", file=sys.stderr)
        sys.exit(1)

    normalized = normalize_config(cfg)
    try:
        result = run_vnpy_backtest(normalized)
    except Exception as exc:
        print(f"vn.py 回测失败：{exc}", file=sys.stderr)
        raise

    summary = result.get("summary", {})
    print("=== vn.py Summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _print_response(resp: requests.Response) -> None:
    try:
        data = resp.json()
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        print(resp.text)
    if not resp.ok:
        sys.exit(resp.status_code if resp.status_code else 1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Options Backtest CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    submit = sub.add_parser("submit", help="提交回测任务")
    submit.add_argument("config", help="配置文件路径 (JSON)")
    submit.add_argument("--idempotency-key", help="可选幂等键")
    submit.set_defaults(func=cmd_submit)

    status = sub.add_parser("status", help="查询回测状态")
    status.add_argument("run_id", help="回测 run_id")
    status.set_defaults(func=cmd_status)

    artifacts = sub.add_parser("artifacts", help="列出回测产物")
    artifacts.add_argument("run_id", help="回测 run_id")
    artifacts.set_defaults(func=cmd_artifacts)

    cancel = sub.add_parser("cancel", help="取消回测")
    cancel.add_argument("run_id", help="回测 run_id")
    cancel.set_defaults(func=cmd_cancel)

    validate = sub.add_parser("validate", help="校验配置")
    validate.add_argument("config", help="配置文件路径 (JSON)")
    validate.set_defaults(func=cmd_validate)

    defaults = sub.add_parser("defaults", help="查看默认阈值")
    defaults.set_defaults(func=cmd_defaults)

    local_run = sub.add_parser("local-run", help="在本地直接执行回测")
    local_run.add_argument("config", help="配置文件路径 (JSON)")
    local_run.add_argument("--run-id", help="自定义 run_id")
    local_run.add_argument("--no-save", action="store_true", help="仅打印结果，不写入 artifacts")
    local_run.add_argument("--trades", type=int, default=10, help="展示的交易条数，默认 10 条 (按时间倒序)")
    local_run.set_defaults(func=cmd_local_run)

    vnpy_run = sub.add_parser("vnpy-run", help="通过 vn.py BacktestingEngine 执行回测")
    vnpy_run.add_argument("config", help="配置文件路径 (JSON)")
    vnpy_run.set_defaults(func=cmd_vnpy_run)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
