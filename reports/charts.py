from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import matplotlib.pyplot as plt
import pandas as pd

from core.engine.artifacts import ensure_dir


def generate_charts(result: Dict, run_dir: Path) -> List[Path]:
    events = result.get("pnl_events") or []
    if not events:
        return []

    df = pd.DataFrame(events)
    if df.empty:
        return []

    try:
        df["date"] = pd.to_datetime(df["date"])
    except Exception:
        return []

    df.sort_values("date", inplace=True)
    df["cum_pnl"] = df["pnl"].cumsum()
    df["peak"] = df["cum_pnl"].cummax()
    df["drawdown"] = df["cum_pnl"] - df["peak"]

    charts_dir = Path(run_dir) / "charts"
    ensure_dir(str(charts_dir))

    generated: List[Path] = []

    # Equity + Drawdown
    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(10, 6))
    axes[0].plot(df["date"], df["cum_pnl"], label="Equity", color="tab:blue")
    axes[0].set_ylabel("Cumulative PnL")
    axes[0].legend(loc="upper left")
    axes[0].grid(True, alpha=0.3)

    axes[1].fill_between(df["date"], df["drawdown"], color="salmon", alpha=0.6)
    axes[1].set_ylabel("Drawdown")
    axes[1].set_xlabel("Date")
    axes[1].grid(True, alpha=0.3)

    fig.suptitle("Equity Curve & Drawdown", fontsize=14)
    fig.autofmt_xdate()
    fig.tight_layout()
    equity_path = charts_dir / "equity_drawdown.png"
    fig.savefig(equity_path, dpi=150)
    plt.close(fig)
    generated.append(equity_path)

    # PnL distribution
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(df["pnl"], bins=min(30, max(5, len(df) // 2)), color="steelblue", alpha=0.85, edgecolor="white")
    ax.set_xlabel("Trade PnL")
    ax.set_ylabel("Frequency")
    ax.set_title("PnL Distribution")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    dist_path = charts_dir / "pnl_distribution.png"
    fig.savefig(dist_path, dpi=150)
    plt.close(fig)
    generated.append(dist_path)

    return generated

