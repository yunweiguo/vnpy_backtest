#!/usr/bin/env python3
"""最小回归脚本：执行一次本地回测并校验基础结果。

用法：
  python scripts/run_smoke.py --config examples/config_csp_spv_us.json

可结合 CI / 定时任务运行，确保回测链路不中断。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.main import ConfigModel, normalize_config
from settings import load_settings
from core.logging_config import configure_logging
from worker.backtest_core import execute_backtest


def main() -> None:
    parser = argparse.ArgumentParser(description="Options Backtest smoke run")
    parser.add_argument("--config", required=True, help="配置文件 (JSON)")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"配置文件不存在：{config_path}", file=sys.stderr)
        sys.exit(1)

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    cfg = ConfigModel.model_validate(raw)
    normalized = normalize_config(cfg)

    settings = load_settings()
    configure_logging(settings.log_level, settings.log_file, settings.log_console)
    result = execute_backtest(normalized, settings)

    summary = result.get("summary", {})
    trades = result.get("trades", [])

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not trades:
        print("[SMOKE] 未产生任何交易记录，回测可能存在问题。", file=sys.stderr)
        sys.exit(2)

    if summary.get("decisions", 0) == 0:
        print("[SMOKE] 决策数为 0，回测流程可能未执行。", file=sys.stderr)
        sys.exit(3)

    print("[SMOKE] 回测通过, 总交易数 =", len(trades))


if __name__ == "__main__":
    main()
