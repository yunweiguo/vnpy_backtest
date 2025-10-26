from __future__ import annotations

import json
import os
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.engine.artifacts import ensure_dir, write_csv, write_json, write_jsonl
from core.data.provider import DataProviderConfig, MySQLProvider, LiquidityGate
from core.strategy.selector_csp_spv import csp_candidates, spv_candidates
from core.strategy.positions import (
    LegPosition,
    PositionState,
    compute_position_metrics,
    format_event,
    legs_snapshot,
    leg_contract_map,
)
from core.utils.timezone import tz_for_market
from reports.charts import generate_charts


def _parse_range(value: Any, default: Tuple[int, int]) -> Tuple[int, int]:
    if value is None:
        return default
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return int(value[0]), int(value[1])
    if isinstance(value, str):
        tokens = value.replace(" ", "").split("-")
        if len(tokens) == 2:
            try:
                return int(float(tokens[0])), int(float(tokens[1]))
            except ValueError:
                return default
    return default


def execute_backtest(config: Dict[str, Any], settings) -> Dict[str, Any]:
    strategy = config["strategy"]
    market = strategy["market"]
    symbols: List[str] = strategy.get("symbols") or []
    tz = strategy.get("tz") or tz_for_market(market)
    selector = config.get("selector", {})
    target_dte: Tuple[int, int] = tuple(selector.get("target_dte_tuple", (30, 60)))  # type: ignore
    short_delta: Tuple[float, float] = tuple(selector.get("short_delta_tuple", (0.18, 0.25)))  # type: ignore
    width_cfg = selector.get("width") or {"min": 2, "max": 8}
    width_range = (int(width_cfg.get("min", 2)), int(width_cfg.get("max", 8)))
    min_cow = float(selector.get("min_credit_of_width", 0.33))

    entry = config.get("entry", {})
    liq_cfg = entry.get("liquidity", {"min_oi": 500, "min_volume": 100, "max_spread_pct": 0.08})
    gate = LiquidityGate(
        min_oi=int(liq_cfg.get("min_oi", 500)),
        min_volume=int(liq_cfg.get("min_volume", 100)),
        max_spread_pct=float(liq_cfg.get("max_spread_pct", 0.08)),
    )

    exit_policy = config.get("exit_policy", {})
    hard_exit_dte = int(exit_policy.get("hard_exit_dte_lte", 7))
    credit_cfg = exit_policy.get("tp_sl", {}).get("credit", {}) if isinstance(exit_policy.get("tp_sl"), dict) else {}
    tp_values = credit_cfg.get("tp_of_max") or []
    sl_values = credit_cfg.get("sl_x_credit") or []
    take_profit_threshold = float(tp_values[0]) if tp_values else None
    stop_loss_multiple = float(sl_values[0]) if sl_values else None
    liquidity_fallback_exit = bool(exit_policy.get("liquidity_fallback_exit", True))

    roll_policy = config.get("roll_policy", {}) or {}
    manage_at_dte = roll_policy.get("manage_at_dte_lte")
    manage_at_dte = int(manage_at_dte) if manage_at_dte is not None else None
    roll_to_range = _parse_range(roll_policy.get("roll_to_dte_tuple") or roll_policy.get("roll_to_dte"), target_dte)

    bt = config.get("backtest", {})
    start_s = bt.get("start")
    end_s = bt.get("end")
    if start_s and end_s:
        start = date.fromisoformat(start_s)
        end = date.fromisoformat(end_s)
    else:
        end = date.today()
        start = end - timedelta(days=120)

    provider = MySQLProvider(
        DataProviderConfig(
            mysql_stock_dsn=settings.mysql_stock_dsn,
            mysql_option_qs_us_dsn=settings.mysql_option_qs_us_dsn,
            mysql_option_qs_hk_dsn=settings.mysql_option_qs_hk_dsn,
            mysql_option_history_dsn=settings.mysql_option_history_dsn,
        )
    )

    decisions_rows: List[Dict[str, Any]] = []
    trades_records: List[Dict[str, Any]] = []
    pnl_events: List[Dict[str, Any]] = []
    chain_records: List[Dict[str, Any]] = []
    active_chains: Dict[str, Dict[str, Any]] = {}
    active_positions: Dict[str, PositionState] = {}

    summary = {
        "entries": 0,
        "exits": 0,
        "rolls": 0,
        "net_pnl": 0.0,
        "decisions": 0,
    }

    def get_chain(symbol: str, kind: str) -> Dict[str, Any]:
        existing = active_chains.get(symbol)
        if existing and existing.get("status") == "ACTIVE":
            return existing
        chain_record = {
            "chain_id": str(uuid.uuid4()),
            "symbol": symbol,
            "kind": kind,
            "status": "ACTIVE",
            "events": [],
        }
        chain_records.append(chain_record)
        active_chains[symbol] = chain_record
        return chain_record

    def record_decision(row: Dict[str, Any], position: PositionState | None = None, chain_id: str | None = None) -> None:
        if position is not None:
            row.setdefault("position_id", position.position_id)
            row.setdefault("chain_id", position.chain_id)
            row.setdefault("kind", position.kind)
            row.setdefault("symbol", position.symbol)
        elif chain_id is not None:
            row.setdefault("chain_id", chain_id)
        decisions_rows.append(row)
        summary["decisions"] += 1

    def append_trade(
        date_str: str,
        symbol: str,
        action: str,
        kind: str,
        position_id: str,
        chain_id: str,
        net_credit: float | None,
        pnl: float | None,
        details: Dict[str, Any],
    ) -> None:
        record = {
            "date": date_str,
            "symbol": symbol,
            "action": action,
            "kind": kind,
            "chain_id": chain_id,
            "position_id": position_id,
            "net_credit": net_credit,
            "pnl": pnl,
            "details": details,
        }
        trades_records.append(record)
        if pnl is not None:
            pnl_events.append({
                "date": date_str,
                "symbol": symbol,
                "kind": kind,
                "action": action,
                "pnl": pnl,
                "chain_id": chain_id,
            })

    def create_position(symbol: str, kind: str, info: Dict[str, Any], entry_date: date, chain_id: str) -> PositionState:
        position_id = str(uuid.uuid4())
        legs: List[LegPosition] = []
        if kind == "CSP":
            expiry = date.fromisoformat(info["expiry"]) if isinstance(info.get("expiry"), str) else entry_date
            legs.append(
                LegPosition(
                    target_id=int(info["target_id"]),
                    side="short",
                    quantity=1,
                    entry_mid=float(info["mid"]),
                    entry_mark=float(info.get("mark") or info["mid"]),
                    multiplier=int(info.get("multiplier", 100)),
                    expiry=expiry,
                    strike=float(info["strike"]),
                    right=str(info.get("right", "put")),
                )
            )
        else:
            expiry = date.fromisoformat(info["expiry"]) if isinstance(info.get("expiry"), str) else entry_date
            multiplier = int(info.get("multiplier", 100))
            legs.append(
                LegPosition(
                    target_id=int(info["short_target_id"]),
                    side="short",
                    quantity=1,
                    entry_mid=float(info["short_mid"]),
                    entry_mark=float(info.get("short_mark") or info["short_mid"]),
                    multiplier=multiplier,
                    expiry=expiry,
                    strike=float(info["short_strike"]),
                    right="put",
                )
            )
            legs.append(
                LegPosition(
                    target_id=int(info["long_target_id"]),
                    side="long",
                    quantity=1,
                    entry_mid=float(info["long_mid"]),
                    entry_mark=float(info.get("long_mark") or info["long_mid"]),
                    multiplier=multiplier,
                    expiry=expiry,
                    strike=float(info["long_strike"]),
                    right="put",
                )
            )
        return PositionState(
            position_id=position_id,
            chain_id=chain_id,
            symbol=symbol,
            kind=kind,
            entry_date=entry_date,
            legs=legs,
            entry_info=info,
        )

    cur = start
    while cur <= end:
        # Skip weekends (no trading sessions) to避免误判行情缺失
        if cur.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
            cur += timedelta(days=1)
            continue

        for sym in symbols:
            position = active_positions.get(sym)
            date_str = str(cur)

            if position:
                expiry_map = {leg.target_id: leg.expiry for leg in position.legs}
                quotes = provider.load_leg_quotes([leg.target_id for leg in position.legs], cur, market, target_expiries=expiry_map)
                metrics = compute_position_metrics(position, quotes)
                if metrics["missing_quotes"]:
                    contract_map = leg_contract_map(position)
                    missing_detail = [
                        {
                            "target_id": tid,
                            "contract_id": contract_map.get(tid),
                        }
                        for tid in metrics["missing_quotes"]
                    ]
                    record_decision(
                        {
                            "date": date_str,
                            "action": "SKIP",
                            "reason": "missing_quotes",
                            "missing": metrics["missing_quotes"],
                            "missing_contracts": missing_detail,
                        },
                        position=position,
                    )
                    continue

                action = None
                reason = None
                roll_candidate = None

                if metrics["min_dte"] is not None and metrics["min_dte"] <= hard_exit_dte:
                    action = "EXIT"
                    reason = "hard_exit_dte"
                elif take_profit_threshold is not None and metrics["profit_pct"] is not None and metrics["profit_pct"] >= take_profit_threshold:
                    action = "EXIT"
                    reason = "take_profit"
                elif stop_loss_multiple is not None and metrics["profit_pct"] is not None and metrics["profit_pct"] <= -stop_loss_multiple:
                    action = "EXIT"
                    reason = "stop_loss"
                else:
                    in_manage = manage_at_dte is not None and metrics["min_dte"] is not None and metrics["min_dte"] <= manage_at_dte
                    if in_manage:
                        if position.kind == "CSP":
                            roll_candidates = csp_candidates(provider, sym, cur, market, roll_to_range, short_delta, gate, top_k=1)
                            short_target = next((leg.target_id for leg in position.legs if leg.side == "short"), None)
                            roll_candidates = [cand for cand in roll_candidates if cand.info.get("target_id") != short_target]
                        else:
                            roll_candidates = spv_candidates(provider, sym, cur, market, roll_to_range, short_delta, width_range, min_cow, gate, top_k=1)
                            short_target = next((leg.target_id for leg in position.legs if leg.side == "short"), None)
                            roll_candidates = [cand for cand in roll_candidates if cand.info.get("short_target_id") != short_target]
                        if roll_candidates:
                            roll_candidate = roll_candidates[0]
                            action = "ROLL"
                            reason = "roll_candidate"
                        elif liquidity_fallback_exit:
                            action = "EXIT"
                            reason = "manage_window_no_roll"

                if action == "EXIT":
                    chain_rec = active_chains.get(sym)
                    if chain_rec:
                        chain_rec["events"].append(
                            format_event("EXIT", date_str, reason or "exit", metrics, {"legs": legs_snapshot(position, quotes)})
                        )
                        chain_rec["status"] = "CLOSED"
                    position.status = "CLOSED"
                    record_decision(
                        {
                            "date": date_str,
                            "action": "EXIT",
                            "reason": reason,
                            "metrics": metrics,
                        },
                        position=position,
                    )
                    append_trade(
                        date_str,
                        sym,
                        "EXIT",
                        position.kind,
                        position.position_id,
                        position.chain_id,
                        metrics["entry_credit"],
                        metrics["pnl"],
                        {"reason": reason, "legs": legs_snapshot(position, quotes)},
                    )
                    summary["exits"] += 1
                    summary["net_pnl"] += metrics["pnl"]
                    active_positions.pop(sym, None)
                    active_chains.pop(sym, None)
                    continue

                if action == "ROLL" and roll_candidate is not None:
                    chain_rec = get_chain(sym, position.kind)
                    legs_info = legs_snapshot(position, quotes)
                    chain_rec["events"].append(
                        format_event("ROLL_EXIT", date_str, reason or "roll_exit", metrics, {"legs": legs_info})
                    )
                    position.status = "ROLLED"
                    record_decision(
                        {
                            "date": date_str,
                            "action": "ROLL_EXIT",
                            "reason": reason,
                            "metrics": metrics,
                        },
                        position=position,
                    )
                    append_trade(
                        date_str,
                        sym,
                        "ROLL_EXIT",
                        position.kind,
                        position.position_id,
                        position.chain_id,
                        metrics["entry_credit"],
                        metrics["pnl"],
                        {"reason": reason, "legs": legs_info},
                    )
                    summary["rolls"] += 1
                    summary["net_pnl"] += metrics["pnl"]

                    new_position = create_position(sym, roll_candidate.kind, roll_candidate.info, cur, position.chain_id)
                    entry_credit = new_position.entry_credit()
                    entry_event = format_event(
                        "ROLL_ENTRY",
                        date_str,
                        "roll_open",
                        {"entry_credit": entry_credit},
                        {"candidate": roll_candidate.info, "legs": legs_snapshot(new_position)},
                    )
                    chain_rec["events"].append(entry_event)
                    new_position.events.append(entry_event)
                    active_positions[sym] = new_position
                    record_decision(
                        {
                            "date": date_str,
                            "action": "ROLL_ENTRY",
                            "score": roll_candidate.score,
                            "info": roll_candidate.info,
                            "metrics": {"entry_credit": entry_credit},
                        },
                        position=new_position,
                    )
                    append_trade(
                        date_str,
                        sym,
                        "ROLL_ENTRY",
                        new_position.kind,
                        new_position.position_id,
                        new_position.chain_id,
                        entry_credit,
                        None,
                        {"candidate": roll_candidate.info, "legs": legs_snapshot(new_position)},
                    )
                    continue

            if sym not in active_positions:
                csp_cands = csp_candidates(provider, sym, cur, market, target_dte, short_delta, gate, top_k=1)
                spv_cands = spv_candidates(provider, sym, cur, market, target_dte, short_delta, width_range, min_cow, gate, top_k=1)

                picked = None
                if csp_cands:
                    picked = csp_cands[0]
                if spv_cands and (picked is None or spv_cands[0].score > picked.score):
                    picked = spv_cands[0]

                if picked:
                    chain_rec = get_chain(sym, picked.kind)
                    new_position = create_position(sym, picked.kind, picked.info, cur, chain_rec["chain_id"])
                    entry_credit = new_position.entry_credit()
                    entry_event = format_event(
                        "ENTRY",
                        date_str,
                        "entry_selected",
                        {"entry_credit": entry_credit},
                        {"candidate": picked.info, "legs": legs_snapshot(new_position)},
                    )
                    chain_rec["events"].append(entry_event)
                    new_position.events.append(entry_event)
                    active_positions[sym] = new_position
                    record_decision(
                        {
                            "date": date_str,
                            "action": "ENTRY",
                            "score": picked.score,
                            "info": picked.info,
                            "metrics": {"entry_credit": entry_credit},
                        },
                        position=new_position,
                    )
                    append_trade(
                        date_str,
                        sym,
                        "ENTRY",
                        picked.kind,
                        new_position.position_id,
                        new_position.chain_id,
                        entry_credit,
                        None,
                        {"candidate": picked.info, "legs": legs_snapshot(new_position)},
                    )
                    summary["entries"] += 1

        cur += timedelta(days=1)

    summary["chains"] = len(chain_records)
    summary["open_positions_end"] = len(active_positions)
    summary["symbols"] = symbols
    summary["start_date"] = str(start)
    summary["end_date"] = str(end)

    if pnl_events:
        sorted_events = sorted(pnl_events, key=lambda x: x["date"])
        equity = 0.0
        peak = 0.0
        max_drawdown = 0.0
        pnl_values = []
        for ev in sorted_events:
            pnl = float(ev["pnl"])
            pnl_values.append(pnl)
            equity += pnl
            peak = max(peak, equity)
            drawdown = equity - peak
            if drawdown < max_drawdown:
                max_drawdown = drawdown
        wins = [p for p in pnl_values if p > 0]
        losses = [p for p in pnl_values if p < 0]
        summary["net_pnl"] = equity
        summary["max_drawdown"] = max_drawdown
        summary["trades_realized"] = len(pnl_values)
        summary["win_rate"] = (len(wins) / len(pnl_values)) if pnl_values else 0.0
        summary["avg_win"] = (sum(wins) / len(wins)) if wins else 0.0
        summary["avg_loss"] = (sum(losses) / len(losses)) if losses else 0.0

    return {
        "summary": summary,
        "decisions": decisions_rows,
        "trades": trades_records,
        "chains": chain_records,
        "pnl_events": pnl_events,
        "config": config,
    }


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
