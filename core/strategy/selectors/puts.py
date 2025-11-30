from __future__ import annotations

from typing import List, Tuple

from core.data.provider import LiquidityGate, MySQLProvider
from core.strategy.selector_csp_spv import csp_candidates, spv_candidates


def fetch_csp(
    provider: MySQLProvider,
    symbol: str,
    session_date,
    market: str,
    target_dte: Tuple[int, int],
    delta_range: Tuple[float, float],
    width_range: Tuple[int, int],
    min_cow: float,
    max_debit_of_width: float,
    gate: LiquidityGate,
    top_k: int,
) -> List:
    return csp_candidates(provider, symbol, session_date, market, target_dte, delta_range, gate, top_k=top_k)


def fetch_spv(
    provider: MySQLProvider,
    symbol: str,
    session_date,
    market: str,
    target_dte: Tuple[int, int],
    delta_range: Tuple[float, float],
    width_range: Tuple[int, int],
    min_cow: float,
    max_debit_of_width: float,
    gate: LiquidityGate,
    top_k: int,
) -> List:
    return spv_candidates(provider, symbol, session_date, market, target_dte, delta_range, width_range, min_cow, gate, top_k=top_k)
