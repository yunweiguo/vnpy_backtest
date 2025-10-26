from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from core.engine.artifacts import ensure_dir, write_csv, write_json, write_jsonl
from core.strategy.runtime import StrategyRuntime
from reports.charts import generate_charts


def execute_backtest(config: Dict[str, Any], settings) -> Dict[str, Any]:
    runtime = StrategyRuntime(config, settings)
    runtime.run_full()
    return runtime.finalize()


def save_artifacts(run_id: str, result: Dict[str, Any], root: str) -> List[Path]:
    run_dir = Path(root) / run_id
    ensure_dir(str(run_dir))
    decisions_path = run_dir / "decisions.jsonl"
    trades_path = run_dir / "trades.csv"
    chain_path = run_dir / "chain.json"
    metrics_path = run_dir / "metrics.json"

    write_jsonl(str(decisions_path), result["decisions"])

    header = [
        "date",
        "symbol",
        "action",
        "kind",
        "chain_id",
        "position_id",
        "net_credit",
        "pnl",
        "details_json",
    ]
    rows = []
    for record in result["trades"]:
        rows.append([
            record["date"],
            record["symbol"],
            record["action"],
            record["kind"],
            record["chain_id"],
            record["position_id"],
            "" if record["net_credit"] is None else f"{record['net_credit']:.4f}",
            "" if record["pnl"] is None else f"{record['pnl']:.4f}",
            json.dumps(record["details"], ensure_ascii=False),
        ])
    write_csv(str(trades_path), header, rows)
    write_json(str(chain_path), {"chains": result["chains"]})
    write_json(str(metrics_path), result["summary"])

    charts = generate_charts(result, run_dir)
    return charts
