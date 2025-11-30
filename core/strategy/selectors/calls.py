from __future__ import annotations

from typing import List, Tuple

from core.data.provider import LiquidityGate, MySQLProvider
from core.strategy.selector_csp_spv import scv_candidates, lcv_candidates


def fetch_scv(
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
    return scv_candidates(provider, symbol, session_date, market, target_dte, delta_range, width_range, min_cow, gate, top_k=top_k)


def fetch_lcv(
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
    return lcv_candidates(provider, symbol, session_date, market, target_dte, delta_range, width_range, max_debit_of_width, gate, top_k=top_k)
