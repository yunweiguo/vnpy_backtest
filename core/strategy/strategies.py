from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List

from core.strategy.positions import LegPosition, PositionState
from core.strategy.registry import display_name


@dataclass
class FillGuardRules:
    max_spread_pct: float
    min_oi: int
    min_volume: int


def filter_roll_candidates(kind: str, candidates: List, short_targets: List[int]) -> List:
    if kind == "CSP":
        primary = short_targets[0] if short_targets else None
        return [cand for cand in candidates if cand.info.get("target_id") != primary]
    if kind == "IC":
        return [
            cand
            for cand in candidates
            if cand.info.get("short_put_id") not in short_targets
            and cand.info.get("short_call_id") not in short_targets
        ]
    primary = short_targets[0] if short_targets else None
    return [cand for cand in candidates if cand.info.get("short_target_id") != primary]


def build_position(kind: str, info: Dict, entry_date: date, chain_id: str, symbol: str) -> PositionState:
    legs: List[LegPosition] = []
    if kind == "CSP":
        expiry = date.fromisoformat(info["expiry"]) if isinstance(info.get("expiry"), str) else entry_date
        legs.append(
            LegPosition(
                target_id=int(info["target_id"]),
                side="short",
                quantity=1,
                entry_mid=float(info.get("fill_mid", info.get("mid", 0.0))),
                entry_mark=float(info.get("fill_mark") or info.get("mark") or info.get("mid", 0.0)),
                multiplier=int(info.get("multiplier", 100)),
                expiry=expiry,
                strike=float(info["strike"]),
                right=str(info.get("right", "put")),
            )
        )
    elif kind == "IC":
        expiry = date.fromisoformat(info["expiry"]) if isinstance(info.get("expiry"), str) else entry_date
        multiplier = int(info.get("multiplier", 100))
        legs.append(
            LegPosition(
                target_id=int(info["short_put_id"]),
                side="short",
                quantity=1,
                entry_mid=float(info.get("short_put_fill", info.get("short_put_mid", 0.0))),
                entry_mark=float(info.get("short_put_fill", info.get("short_put_mark", 0.0))),
                multiplier=multiplier,
                expiry=expiry,
                strike=float(info["short_put_strike"]),
                right="put",
            )
        )
        legs.append(
            LegPosition(
                target_id=int(info["long_put_id"]),
                side="long",
                quantity=1,
                entry_mid=float(info.get("long_put_fill", info.get("long_put_mid", 0.0))),
                entry_mark=float(info.get("long_put_fill", info.get("long_put_mark", 0.0))),
                multiplier=multiplier,
                expiry=expiry,
                strike=float(info["long_put_strike"]),
                right="put",
            )
        )
        legs.append(
            LegPosition(
                target_id=int(info["short_call_id"]),
                side="short",
                quantity=1,
                entry_mid=float(info.get("short_call_fill", info.get("short_call_mid", 0.0))),
                entry_mark=float(info.get("short_call_fill", info.get("short_call_mark", 0.0))),
                multiplier=multiplier,
                expiry=expiry,
                strike=float(info["short_call_strike"]),
                right="call",
            )
        )
        legs.append(
            LegPosition(
                target_id=int(info["long_call_id"]),
                side="long",
                quantity=1,
                entry_mid=float(info.get("long_call_fill", info.get("long_call_mid", 0.0))),
                entry_mark=float(info.get("long_call_fill", info.get("long_call_mark", 0.0))),
                multiplier=multiplier,
                expiry=expiry,
                strike=float(info["long_call_strike"]),
                right="call",
            )
        )
    else:
        expiry = date.fromisoformat(info["expiry"]) if isinstance(info.get("expiry"), str) else entry_date
        multiplier = int(info.get("multiplier", 100))
        short_right = str(info.get("short_right", "put"))
        long_right = str(info.get("long_right", "put"))
        legs.append(
            LegPosition(
                target_id=int(info["short_target_id"]),
                side="short",
                quantity=1,
                entry_mid=float(info.get("short_fill_mid", info.get("short_mid", 0.0))),
                entry_mark=float(info.get("short_fill_mark") or info.get("short_mark") or info.get("short_mid", 0.0)),
                multiplier=multiplier,
                expiry=expiry,
                strike=float(info["short_strike"]),
                right=short_right,
            )
        )
        legs.append(
            LegPosition(
                target_id=int(info["long_target_id"]),
                side="long",
                quantity=1,
                entry_mid=float(info.get("long_fill_mid", info.get("long_mid", 0.0))),
                entry_mark=float(info.get("long_fill_mark") or info.get("long_mark") or info.get("long_mid", 0.0)),
                multiplier=multiplier,
                expiry=expiry,
                strike=float(info["long_strike"]),
                right=long_right,
            )
        )

    return PositionState(
        position_id=info.get("position_id") or "",  # runtime will overwrite with uuid
        chain_id=chain_id,
        symbol=symbol,
        kind=kind,
        entry_date=entry_date,
        legs=legs,
        entry_info=info,
    )


def apply_fill_prices(kind: str, info: Dict, price_cb) -> None:
    if kind == "CSP":
        fill = price_cb("short", info.get("mid", 0.0), info.get("bid"), info.get("ask"))
        info["fill_mid"] = fill
        info["fill_mark"] = fill
        return
    if kind == "IC":
        info["short_put_fill"] = price_cb("short", info.get("short_put_mid", 0.0), info.get("short_put_bid"), info.get("short_put_ask"))
        info["long_put_fill"] = price_cb("long", info.get("long_put_mid", 0.0), info.get("long_put_bid"), info.get("long_put_ask"))
        info["short_call_fill"] = price_cb("short", info.get("short_call_mid", 0.0), info.get("short_call_bid"), info.get("short_call_ask"))
        info["long_call_fill"] = price_cb("long", info.get("long_call_mid", 0.0), info.get("long_call_bid"), info.get("long_call_ask"))
        return
    short_fill = price_cb("short", info.get("short_mid", 0.0), info.get("short_bid"), info.get("short_ask"))
    long_fill = price_cb("long", info.get("long_mid", 0.0), info.get("long_bid"), info.get("long_ask"))
    info["short_fill_mid"] = short_fill
    info["short_fill_mark"] = short_fill
    info["long_fill_mid"] = long_fill
    info["long_fill_mark"] = long_fill


def check_fill_guard(kind: str, info: Dict, rules: FillGuardRules):
    max_spread_pct = rules.max_spread_pct
    min_oi = rules.min_oi
    min_volume = rules.min_volume

    def _leg_ok(prefix: str, mark, bid, ask, oi, vol):
        if oi is not None and oi < min_oi:
            return False, f"{prefix}_min_oi"
        if vol is not None and vol < min_volume:
            return False, f"{prefix}_min_volume"
        if mark and bid is not None and ask is not None and mark > 0:
            spread = max(0.0, float(ask) - float(bid))
            if spread > 0 and (spread / float(mark)) > max_spread_pct:
                return False, f"{prefix}_spread"
        return True, None

    if kind == "CSP":
        return _leg_ok("short", info.get("mark") or info.get("mid"), info.get("bid"), info.get("ask"), info.get("oi"), info.get("volume"))
    if kind == "IC":
        legs = [
            ("short_put", info.get("short_put_mark") or info.get("short_put_mid"), info.get("short_put_bid"), info.get("short_put_ask"), info.get("short_put_oi"), info.get("short_put_volume")),
            ("long_put", info.get("long_put_mark") or info.get("long_put_mid"), info.get("long_put_bid"), info.get("long_put_ask"), info.get("long_put_oi"), info.get("long_put_volume")),
            ("short_call", info.get("short_call_mark") or info.get("short_call_mid"), info.get("short_call_bid"), info.get("short_call_ask"), info.get("short_call_oi"), info.get("short_call_volume")),
            ("long_call", info.get("long_call_mark") or info.get("long_call_mid"), info.get("long_call_bid"), info.get("long_call_ask"), info.get("long_call_oi"), info.get("long_call_volume")),
        ]
        for prefix, mark, bid, ask, oi, vol in legs:
            ok, reason = _leg_ok(prefix, mark, bid, ask, oi, vol)
            if not ok:
                return False, reason
        return True, None
    short_ok, short_reason = _leg_ok(
        "short",
        info.get("short_mark") or info.get("short_mid"),
        info.get("short_bid"),
        info.get("short_ask"),
        info.get("short_oi"),
        info.get("short_volume"),
    )
    if not short_ok:
        return False, short_reason
    return _leg_ok(
        "long",
        info.get("long_mark") or info.get("long_mid"),
        info.get("long_bid"),
        info.get("long_ask"),
        info.get("long_oi"),
        info.get("long_volume"),
    )


def kind_label(kind: str) -> str:
    return display_name(kind)


def can_add_wing(kind: str) -> bool:
    # 目前仅允许 CSP 加保护翼；其他策略可按需开启
    return kind == "CSP"


def decide_exit_and_manage(
    kind: str,
    metrics: Dict,
    hard_exit_dte: int,
    tp_threshold: float | None,
    sl_multiple: float | None,
    manage_at_dte: int | None,
    delta_roll_trigger: float,
    delta_force_exit: float,
) -> Dict[str, object]:
    """Return action/flags for exit or manage window based on current metrics.

    Output keys: action (EXIT/None), reason (str|None), in_manage (bool), manage_reason (str|None).
    """
    min_dte = metrics.get("min_dte")
    short_delta = metrics.get("short_leg_delta_abs")
    profit_pct = metrics.get("profit_pct")

    hard_exit = min_dte is not None and min_dte <= hard_exit_dte
    take_profit_hit = tp_threshold is not None and profit_pct is not None and profit_pct >= tp_threshold
    stop_loss_hit = sl_multiple is not None and profit_pct is not None and profit_pct <= -sl_multiple
    threatened = short_delta is not None and short_delta >= delta_roll_trigger
    force_exit_delta = short_delta is not None and short_delta >= delta_force_exit

    if hard_exit:
        return {"action": "EXIT", "reason": "hard_exit_dte", "in_manage": False, "manage_reason": None}
    if take_profit_hit:
        return {"action": "EXIT", "reason": "take_profit", "in_manage": False, "manage_reason": None}
    if stop_loss_hit:
        return {"action": "EXIT", "reason": "stop_loss", "in_manage": False, "manage_reason": None}
    if force_exit_delta:
        return {"action": "EXIT", "reason": "delta_force_exit", "in_manage": False, "manage_reason": None}

    in_manage = False
    manage_reason = None
    if manage_at_dte is not None and min_dte is not None and min_dte <= manage_at_dte:
        in_manage = True
        manage_reason = "dte_manage_window"
    if threatened:
        in_manage = True
        if not manage_reason:
            manage_reason = "delta_threat"

    return {"action": None, "reason": None, "in_manage": in_manage, "manage_reason": manage_reason}
