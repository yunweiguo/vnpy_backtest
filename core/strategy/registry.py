from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from core.data.provider import LiquidityGate, MySQLProvider


@dataclass
class SelectorSpec:
    kind: str
    fetch: Callable[
        [MySQLProvider, str, object, str, Tuple[int, int], Tuple[float, float], Tuple[int, int], float, float, LiquidityGate, int],
        List,
    ]
    # argument contract:
    # provider, symbol, session_date, market, target_dte, delta_range, width_range, min_cow, max_debit_of_width, gate, top_k


# 人类可读名称，便于日志/文档引用
STRATEGY_DESCRIPTIONS: Dict[str, str] = {
    "CSP": "Cash-Secured Put (备兑现金卖出认沽)",
    "SPV": "Short Put Vertical (卖出看跌价差)",
    "SCV": "Short Call Vertical (卖出看涨价差)",
    "LCV": "Long Call Vertical (买入看涨价差)",
    "IC": "Iron Condor (铁秃鹰)",
}


class SelectorRegistry:
    def __init__(self) -> None:
        self._specs: Dict[str, SelectorSpec] = {}

    def register(self, kind: str, spec: SelectorSpec) -> None:
        self._specs[kind] = spec

    def get(self, kind: str) -> Optional[SelectorSpec]:
        return self._specs.get(kind)

    def kinds(self) -> Iterable[str]:
        return self._specs.keys()


def display_name(kind: str) -> str:
    return STRATEGY_DESCRIPTIONS.get(kind, kind)


registry = SelectorRegistry()
