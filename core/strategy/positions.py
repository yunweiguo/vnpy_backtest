from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from core.models.normalized import OptionRowNormalized


def format_contract_id(symbol: str, expiry: date, strike: float, right: str) -> str:
    expiry_str = expiry.strftime("%Y%m%d")
    strike_str = ("%0.2f" % strike).rstrip("0").rstrip(".")
    return f"{symbol} {expiry_str} {strike_str} {right.upper()}"


def leg_contract_map(position: PositionState) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    for leg in position.legs:
        mapping[leg.target_id] = format_contract_id(position.symbol, leg.expiry, leg.strike, leg.right)
    return mapping


@dataclass
class LegPosition:
    target_id: int
    side: str  # 'short' or 'long'
    quantity: int
    entry_mid: float
    entry_mark: float
    multiplier: int
    expiry: date
    strike: float
    right: str

    @property
    def direction(self) -> int:
        return 1 if self.side == "short" else -1


@dataclass
class PositionState:
    position_id: str
    chain_id: str
    symbol: str
    kind: str  # 'CSP' | 'SPV'
    entry_date: date
    legs: List[LegPosition]
    entry_info: Dict[str, Any]
    status: str = "OPEN"
    events: List[Dict[str, Any]] = field(default_factory=list)
    manage_window_entered: bool = False
    roll_count: int = 0

    def entry_credit(self) -> float:
        return sum(
            leg.entry_mid * leg.multiplier * leg.quantity * leg.direction
            for leg in self.legs
        )


def legs_snapshot(position: PositionState, quotes: Optional[Dict[int, OptionRowNormalized]] = None) -> List[Dict[str, Any]]:
    snap: List[Dict[str, Any]] = []
    for leg in position.legs:
        data = {
            "target_id": leg.target_id,
            "side": leg.side,
            "qty": leg.quantity,
            "entry_mid": leg.entry_mid,
            "entry_mark": leg.entry_mark,
            "multiplier": leg.multiplier,
            "expiry": str(leg.expiry),
            "strike": leg.strike,
            "right": leg.right,
            "contract_id": format_contract_id(position.symbol, leg.expiry, leg.strike, leg.right),
        }
        if quotes and leg.target_id in quotes:
            q = quotes[leg.target_id]
            data.update({
                "current_mid": q.mid,
                "current_mark": q.mark,
                "current_dte": q.dte,
                "current_delta": q.delta,
            })
        snap.append(data)
    return snap


def compute_position_metrics(
    position: PositionState,
    quotes: Dict[int, OptionRowNormalized],
) -> Dict[str, Any]:
    entry_credit = 0.0
    buyback_cost = 0.0
    pnl = 0.0
    dtes: List[int] = []
    missing: List[int] = []
    short_leg_delta_abs: Optional[float] = None

    for leg in position.legs:
        direction = leg.direction
        entry_credit += leg.entry_mid * leg.multiplier * leg.quantity * direction
        quote = quotes.get(leg.target_id)
        if not quote:
            missing.append(leg.target_id)
            continue
        buyback_cost += quote.mid * leg.multiplier * leg.quantity * direction
        pnl += (leg.entry_mid - quote.mid) * direction * leg.multiplier * leg.quantity
        dtes.append(quote.dte)
        if leg.side == "short" and quote.delta is not None:
            delta_abs = abs(quote.delta)
            short_leg_delta_abs = max(short_leg_delta_abs or 0.0, delta_abs)

    profit_pct = (pnl / entry_credit) if entry_credit > 0 else None
    min_dte = min(dtes) if dtes else None
    return {
        "entry_credit": entry_credit,
        "buyback_cost": buyback_cost,
        "pnl": pnl,
        "profit_pct": profit_pct,
        "min_dte": min_dte,
        "missing_quotes": missing,
        "short_leg_delta_abs": short_leg_delta_abs,
    }


def format_event(
    action: str,
    date_str: str,
    reason: str,
    metrics: Optional[Dict[str, Any]] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "date": date_str,
        "action": action,
        "reason": reason,
    }
    if metrics:
        payload["metrics"] = metrics
    if details:
        payload["details"] = details
    return payload
