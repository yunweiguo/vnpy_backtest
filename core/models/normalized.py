from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass
class OptionRowNormalized:
    opt_symbol: str
    target_id: int
    right: str  # 'call' | 'put'
    strike_dec: Decimal
    expiry_local_date: date
    session_local_date: date
    bid: float
    ask: float
    mid: float
    mark: float
    iv: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    volume: Optional[int]
    open_interest: Optional[int]
    multiplier: int
    min_tick: Optional[float]
    dte: int

