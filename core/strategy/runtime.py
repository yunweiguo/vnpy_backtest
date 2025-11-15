from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timedelta
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Tuple

from core.data.provider import DataProviderConfig, LiquidityGate, MySQLProvider
from core.logging_config import get_logger
from core.strategy.positions import (
    LegPosition,
    PositionState,
    compute_position_metrics,
    format_event,
    leg_contract_map,
    legs_snapshot,
)
from core.strategy.selector_csp_spv import csp_candidates, spv_candidates

logger = get_logger(__name__)


class StrategyRuntime:
    """Reusable runtime that drives option strategy decisions bar-by-bar."""

    def __init__(self, config: Dict[str, Any], settings, provider: Optional[MySQLProvider] = None) -> None:
        self.config = config
        self.settings = settings
        self.provider = provider or MySQLProvider(
            DataProviderConfig(
                mysql_stock_dsn=settings.mysql_stock_dsn,
                mysql_option_qs_us_dsn=settings.mysql_option_qs_us_dsn,
                mysql_option_qs_hk_dsn=settings.mysql_option_qs_hk_dsn,
                mysql_option_history_dsn=settings.mysql_option_history_dsn,
            )
        )

        strategy = config["strategy"]
        self.market: str = strategy["market"]
        self.symbols: List[str] = strategy.get("symbols") or []
        self.tz: Optional[str] = strategy.get("tz")

        selector = config.get("selector", {})
        self.target_dte: Tuple[int, int] = tuple(selector.get("target_dte_tuple", (30, 60)))  # type: ignore
        self.short_delta: Tuple[float, float] = tuple(selector.get("short_delta_tuple", (0.18, 0.25)))  # type: ignore
        width_cfg = selector.get("width") or {"min": 2, "max": 8}
        self.width_range: Tuple[int, int] = (int(width_cfg.get("min", 2)), int(width_cfg.get("max", 8)))
        self.min_cow: float = float(selector.get("min_credit_of_width", 0.33))

        entry = config.get("entry", {})
        liq_cfg = entry.get("liquidity", {"min_oi": 500, "min_volume": 100, "max_spread_pct": 0.08})
        self.gate = LiquidityGate(
            min_oi=int(liq_cfg.get("min_oi", 500)),
            min_volume=int(liq_cfg.get("min_volume", 100)),
            max_spread_pct=float(liq_cfg.get("max_spread_pct", 0.08)),
        )

        exit_policy = config.get("exit_policy", {})
        self.hard_exit_dte: int = int(exit_policy.get("hard_exit_dte_lte", 7))
        credit_cfg = exit_policy.get("tp_sl", {}).get("credit", {}) if isinstance(exit_policy.get("tp_sl"), dict) else {}
        tp_values = credit_cfg.get("tp_of_max") or []
        sl_values = credit_cfg.get("sl_x_credit") or []
        self.take_profit_threshold: Optional[float] = float(tp_values[0]) if tp_values else None
        self.stop_loss_multiple: Optional[float] = float(sl_values[0]) if sl_values else None
        self.liquidity_fallback_exit: bool = bool(exit_policy.get("liquidity_fallback_exit", True))

        roll_policy = config.get("roll_policy", {}) or {}
        manage_at_dte = roll_policy.get("manage_at_dte_lte")
        self.manage_at_dte: Optional[int] = int(manage_at_dte) if manage_at_dte is not None else None
        self.roll_to_range: Tuple[int, int] = _parse_range(roll_policy.get("roll_to_dte_tuple") or roll_policy.get("roll_to_dte"), self.target_dte)
        delta_retarget = roll_policy.get("delta_retarget_tuple") or {}
        self.roll_delta_range: Tuple[float, float] = tuple(delta_retarget.get("put", self.short_delta))  # type: ignore

        tolerances = config.get("tolerances", {}) or {}
        self.delta_snap: float = float(tolerances.get("delta_snap", 0.01))
        self.delta_roll_trigger: float = abs(self.short_delta[1]) + self.delta_snap
        self.delta_force_exit: float = self.delta_roll_trigger + self.delta_snap

        bt = config.get("backtest", {})
        start_s = bt.get("start")
        end_s = bt.get("end")
        if start_s and end_s:
            self.start_date = date.fromisoformat(start_s)
            self.end_date = date.fromisoformat(end_s)
        else:
            self.end_date = date.today()
            self.start_date = self.end_date - timedelta(days=120)

        self.session_dates: List[date] = self._generate_session_dates()
        self.reset_state()

    @staticmethod
    def generate_session_dates(config: Dict[str, Any]) -> List[date]:
        bt = config.get("backtest", {})
        start_s = bt.get("start")
        end_s = bt.get("end")
        if start_s and end_s:
            start_date = date.fromisoformat(start_s)
            end_date = date.fromisoformat(end_s)
        else:
            end_date = date.today()
            start_date = end_date - timedelta(days=120)
        return _business_days(start_date, end_date)

    def _generate_session_dates(self) -> List[date]:
        return _business_days(self.start_date, self.end_date)

    def reset_state(self) -> None:
        self.decisions_rows: List[Dict[str, Any]] = []
        self.trades_records: List[Dict[str, Any]] = []
        self.chain_records: List[Dict[str, Any]] = []
        self.active_chains: Dict[str, Dict[str, Any]] = {}
        self.active_positions: Dict[str, PositionState] = {}
        self.pnl_events: List[Dict[str, Any]] = []
        self.entry_credit_history: List[float] = []
        self.summary: Dict[str, Any] = {
            "entries": 0,
            "exits": 0,
            "rolls": 0,
            "roll_attempts": 0,
            "roll_failures": 0,
            "manage_window_entries": 0,
            "forced_exits": 0,
            "net_pnl": 0.0,
            "decisions": 0,
            "annualized_return": None,
            "sharpe_ratio": None,
            "pnl_std": None,
            "max_drawdown_pct": None,
            "avg_entry_credit": None,
            "max_entry_credit": None,
            "duration_days": None,
            "trading_days": None,
        }

    def run_full(self) -> None:
        logger.info(
            "Backtest execution started",
            extra={"symbols": self.symbols, "start": str(self.start_date), "end": str(self.end_date)},
        )
        for session_date in self.session_dates:
            self.process_session(session_date)

    def process_session(self, session_date: date) -> None:
        if session_date.weekday() >= 5:
            return
        date_str = str(session_date)
        for sym in self.symbols:
            position = self.active_positions.get(sym)

            if position:
                expiry_map = {leg.target_id: leg.expiry for leg in position.legs}
                quotes = self.provider.load_leg_quotes(
                    [leg.target_id for leg in position.legs], session_date, self.market, target_expiries=expiry_map
                )
                metrics = compute_position_metrics(position, quotes)
                if metrics["missing_quotes"]:
                    contract_map = leg_contract_map(position)
                    missing_detail = [
                        {"target_id": tid, "contract_id": contract_map.get(tid)}
                        for tid in metrics["missing_quotes"]
                    ]
                    chain_rec = self.active_chains.get(sym)
                    if chain_rec:
                        self._log_chain_event(
                            chain_rec,
                            date_str,
                            "SKIP",
                            "missing_quotes",
                            {"missing": metrics["missing_quotes"]},
                            {"missing_contracts": missing_detail},
                        )
                    logger.debug(
                        "Quotes missing",
                        extra={"date": date_str, "symbol": sym, "position_id": position.position_id, "missing": missing_detail},
                    )
                    self._record_decision(
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
                chain_rec = self.active_chains.get(sym)
                min_dte = metrics.get("min_dte")
                short_delta = metrics.get("short_leg_delta_abs")
                threatened = short_delta is not None and short_delta >= self.delta_roll_trigger
                force_exit_delta = short_delta is not None and short_delta >= self.delta_force_exit
                hard_exit = min_dte is not None and min_dte <= self.hard_exit_dte
                take_profit_hit = (
                    self.take_profit_threshold is not None
                    and metrics["profit_pct"] is not None
                    and metrics["profit_pct"] >= self.take_profit_threshold
                )
                stop_loss_hit = (
                    self.stop_loss_multiple is not None
                    and metrics["profit_pct"] is not None
                    and metrics["profit_pct"] <= -self.stop_loss_multiple
                )

                if hard_exit:
                    action = "EXIT"
                    reason = "hard_exit_dte"
                elif take_profit_hit:
                    action = "EXIT"
                    reason = "take_profit"
                elif stop_loss_hit:
                    action = "EXIT"
                    reason = "stop_loss"
                elif force_exit_delta:
                    action = "EXIT"
                    reason = "delta_force_exit"
                    self.summary["forced_exits"] += 1
                else:
                    in_manage = False
                    manage_reason = None
                    if self.manage_at_dte is not None and min_dte is not None and min_dte <= self.manage_at_dte:
                        in_manage = True
                        manage_reason = "dte_manage_window"
                    if threatened:
                        in_manage = True
                        if not manage_reason:
                            manage_reason = "delta_threat"
                    if in_manage:
                        self._mark_manage_window(position, date_str, metrics, manage_reason or "manage_window")
                        if position.kind == "CSP":
                            roll_candidates = csp_candidates(
                                self.provider,
                                sym,
                                session_date,
                                self.market,
                                self.roll_to_range,
                                self.roll_delta_range,
                                self.gate,
                                top_k=3,
                            )
                            short_target = next((leg.target_id for leg in position.legs if leg.side == "short"), None)
                            roll_candidates = [cand for cand in roll_candidates if cand.info.get("target_id") != short_target]
                        else:
                            roll_candidates = spv_candidates(
                                self.provider,
                                sym,
                                session_date,
                                self.market,
                                self.roll_to_range,
                                self.roll_delta_range,
                                self.width_range,
                                self.min_cow,
                                self.gate,
                                top_k=3,
                            )
                            short_target = next((leg.target_id for leg in position.legs if leg.side == "short"), None)
                            roll_candidates = [
                                cand for cand in roll_candidates if cand.info.get("short_target_id") != short_target
                            ]
                        self._record_roll_attempt(
                            sym,
                            date_str,
                            manage_reason or "manage_window",
                            len(roll_candidates),
                            {"short_leg_delta": short_delta, "min_dte": min_dte},
                        )
                        if roll_candidates:
                            roll_candidate = roll_candidates[0]
                            action = "ROLL"
                            reason = manage_reason or "roll_candidate"
                        elif self.liquidity_fallback_exit:
                            if chain_rec:
                                self._log_chain_event(
                                    chain_rec,
                                    date_str,
                                    "ROLL_SKIPPED",
                                    "no_candidates",
                                    {"min_dte": min_dte, "short_leg_delta": short_delta},
                                )
                            self.summary["roll_failures"] += 1
                            action = "EXIT"
                            reason = "manage_window_no_roll"
                        else:
                            if chain_rec:
                                self._log_chain_event(
                                    chain_rec,
                                    date_str,
                                    "ROLL_SKIPPED",
                                    "no_candidates",
                                    {"min_dte": min_dte, "short_leg_delta": short_delta},
                                )
                            self.summary["roll_failures"] += 1
                            continue

                if action == "EXIT":
                    self._handle_exit(position, sym, date_str, metrics, reason, quotes)
                    continue

                if action == "ROLL" and roll_candidate is not None:
                    self._handle_roll(position, sym, date_str, metrics, reason, roll_candidate, session_date, quotes)
                    continue

            if sym not in self.active_positions:
                self._handle_entry(sym, session_date, date_str)

    def _handle_entry(self, symbol: str, session_date: date, date_str: str) -> None:
        csp_cands = csp_candidates(self.provider, symbol, session_date, self.market, self.target_dte, self.short_delta, self.gate, top_k=1)
        spv_cands = spv_candidates(
            self.provider,
            symbol,
            session_date,
            self.market,
            self.target_dte,
            self.short_delta,
            self.width_range,
            self.min_cow,
            self.gate,
            top_k=1,
        )

        picked = None
        if csp_cands:
            picked = csp_cands[0]
        if spv_cands and (picked is None or spv_cands[0].score > picked.score):
            picked = spv_cands[0]

        if not picked:
            return

        chain_rec = self._get_chain(symbol, picked.kind)
        new_position = self._create_position(symbol, picked.kind, picked.info, session_date, chain_rec["chain_id"])
        entry_credit = new_position.entry_credit()
        chain_rec["opened_at"] = chain_rec.get("opened_at") or date_str
        chain_rec["status"] = "ACTIVE"
        chain_rec["current_position_id"] = new_position.position_id
        self._increment_chain_stat(chain_rec, "entries")
        self.entry_credit_history.append(abs(entry_credit))
        entry_event = self._log_chain_event(
            chain_rec,
            date_str,
            "ENTRY",
            "entry_selected",
            {"entry_credit": entry_credit},
            {"candidate": picked.info, "legs": legs_snapshot(new_position)},
        )
        new_position.events.append(entry_event)
        self.active_positions[symbol] = new_position
        self._record_decision(
            {
                "date": date_str,
                "action": "ENTRY",
                "score": picked.score,
                "info": picked.info,
                "metrics": {"entry_credit": entry_credit},
            },
            position=new_position,
        )
        self._append_trade(
            date_str,
            symbol,
            "ENTRY",
            picked.kind,
            new_position.position_id,
            new_position.chain_id,
            entry_credit,
            None,
            {"candidate": picked.info, "legs": legs_snapshot(new_position)},
        )
        self.summary["entries"] += 1
        logger.info(
            "New position entry",
            extra={"date": date_str, "symbol": symbol, "position_id": new_position.position_id, "kind": picked.kind, "entry_credit": entry_credit},
        )

    def _handle_exit(
        self,
        position: PositionState,
        symbol: str,
        date_str: str,
        metrics: Dict[str, Any],
        reason: Optional[str],
        quotes: Dict[int, Any],
    ) -> None:
        chain_rec = self.active_chains.get(symbol)
        if chain_rec:
            self._log_chain_event(
                chain_rec,
                date_str,
                "EXIT",
                reason or "exit",
                metrics,
                {"legs": legs_snapshot(position, quotes)},
            )
            chain_rec["status"] = "CLOSED"
            chain_rec["closed_at"] = date_str
            chain_rec.pop("current_position_id", None)
            self._increment_chain_stat(chain_rec, "exits")
        position.status = "CLOSED"
        self._record_decision(
            {
                "date": date_str,
                "action": "EXIT",
                "reason": reason,
                "metrics": metrics,
            },
            position=position,
        )
        self._append_trade(
            date_str,
            symbol,
            "EXIT",
            position.kind,
            position.position_id,
            position.chain_id,
            metrics["entry_credit"],
            metrics["pnl"],
            {"reason": reason, "legs": legs_snapshot(position, quotes)},
        )
        self.summary["exits"] += 1
        self.summary["net_pnl"] += metrics["pnl"]
        logger.info(
            "Position exit",
            extra={"date": date_str, "symbol": symbol, "position_id": position.position_id, "reason": reason, "pnl": metrics["pnl"]},
        )
        self.active_positions.pop(symbol, None)
        self.active_chains.pop(symbol, None)

    def _handle_roll(
        self,
        position: PositionState,
        symbol: str,
        date_str: str,
        metrics: Dict[str, Any],
        reason: Optional[str],
        roll_candidate,
        session_date: date,
        quotes: Dict[int, Any],
    ) -> None:
        chain_rec = self._get_chain(symbol, position.kind)
        legs_info = legs_snapshot(position, quotes)
        self._log_chain_event(
            chain_rec,
            date_str,
            "ROLL_EXIT",
            reason or "roll_exit",
            metrics,
            {"legs": legs_info},
        )
        chain_rec["status"] = "ROLLING"
        position.status = "ROLLED"
        position.roll_count += 1
        self._record_decision(
            {
                "date": date_str,
                "action": "ROLL_EXIT",
                "reason": reason,
                "metrics": metrics,
            },
            position=position,
        )
        self._append_trade(
            date_str,
            symbol,
            "ROLL_EXIT",
            position.kind,
            position.position_id,
            position.chain_id,
            metrics["entry_credit"],
            metrics["pnl"],
            {"reason": reason, "legs": legs_info},
        )
        self.summary["rolls"] += 1
        self.summary["net_pnl"] += metrics["pnl"]
        self._increment_chain_stat(chain_rec, "rolls")

        new_position = self._create_position(symbol, roll_candidate.kind, roll_candidate.info, session_date, position.chain_id)
        new_position.roll_count = position.roll_count
        new_position.manage_window_entered = position.manage_window_entered
        entry_credit = new_position.entry_credit()
        self.entry_credit_history.append(abs(entry_credit))
        entry_event = self._log_chain_event(
            chain_rec,
            date_str,
            "ROLL_ENTRY",
            "roll_open",
            {"entry_credit": entry_credit},
            {"candidate": roll_candidate.info, "legs": legs_snapshot(new_position)},
        )
        new_position.events.append(entry_event)
        chain_rec["status"] = "ACTIVE"
        chain_rec["current_position_id"] = new_position.position_id
        self.active_positions[symbol] = new_position
        self._record_decision(
            {
                "date": date_str,
                "action": "ROLL_ENTRY",
                "score": roll_candidate.score,
                "info": roll_candidate.info,
                "metrics": {"entry_credit": entry_credit},
            },
            position=new_position,
        )
        self._append_trade(
            date_str,
            symbol,
            "ROLL_ENTRY",
            new_position.kind,
            new_position.position_id,
            new_position.chain_id,
            entry_credit,
            None,
            {"candidate": roll_candidate.info, "legs": legs_snapshot(new_position)},
        )
        logger.info(
            "Position rolled",
            extra={"date": date_str, "symbol": symbol, "position_id": position.position_id, "new_position_id": new_position.position_id, "pnl": metrics["pnl"]},
        )

    def _create_position(self, symbol: str, kind: str, info: Dict[str, Any], entry_date: date, chain_id: str) -> PositionState:
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

    def _get_chain(self, symbol: str, kind: str) -> Dict[str, Any]:
        existing = self.active_chains.get(symbol)
        if existing:
            return existing
        chain_record = {
            "chain_id": str(uuid.uuid4()),
            "symbol": symbol,
            "kind": kind,
            "status": "ACTIVE",
            "opened_at": None,
            "closed_at": None,
            "stats": {"entries": 0, "rolls": 0, "exits": 0},
            "events": [],
        }
        self.chain_records.append(chain_record)
        self.active_chains[symbol] = chain_record
        return chain_record

    @staticmethod
    def _log_chain_event(
        chain_rec: Dict[str, Any],
        date_str: str,
        action: str,
        reason: Optional[str] = None,
        metrics: Optional[Dict[str, Any]] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event = format_event(action, date_str, reason or action.lower(), metrics, details)
        chain_rec["events"].append(event)
        return event

    @staticmethod
    def _increment_chain_stat(chain_rec: Dict[str, Any], key: str) -> None:
        stats = chain_rec.setdefault("stats", {})
        stats[key] = stats.get(key, 0) + 1

    def _mark_manage_window(
        self,
        position: PositionState,
        date_str: str,
        metrics: Dict[str, Any],
        trigger: str,
    ) -> None:
        if position.manage_window_entered:
            return
        position.manage_window_entered = True
        chain_rec = self.active_chains.get(position.symbol)
        if chain_rec:
            self._log_chain_event(
                chain_rec,
                date_str,
                "MANAGE_WINDOW_ENTER",
                trigger,
                {"min_dte": metrics.get("min_dte"), "short_leg_delta": metrics.get("short_leg_delta_abs")},
            )
        self.summary["manage_window_entries"] += 1

    def _record_roll_attempt(
        self,
        symbol: str,
        date_str: str,
        reason: str,
        candidates: int,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        chain_rec = self.active_chains.get(symbol)
        if chain_rec:
            self._log_chain_event(
                chain_rec,
                date_str,
                "ROLL_CHECK",
                reason,
                {"candidates": candidates, **(metrics or {})} if metrics else {"candidates": candidates},
            )
        self.summary["roll_attempts"] += 1

    def _record_decision(self, row: Dict[str, Any], position: PositionState | None = None, chain_id: str | None = None) -> None:
        if position is not None:
            row.setdefault("position_id", position.position_id)
            row.setdefault("chain_id", position.chain_id)
            row.setdefault("kind", position.kind)
            row.setdefault("symbol", position.symbol)
        elif chain_id is not None:
            row.setdefault("chain_id", chain_id)
        self.decisions_rows.append(row)
        self.summary["decisions"] += 1

    def _append_trade(
        self,
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
        self.trades_records.append(record)
        if pnl is not None:
            self.pnl_events.append({"date": date_str, "symbol": symbol, "kind": kind, "action": action, "pnl": pnl, "chain_id": chain_id})

    def finalize(self) -> Dict[str, Any]:
        self.summary["chains"] = len(self.chain_records)
        self.summary["open_positions_end"] = len(self.active_positions)
        self.summary["symbols"] = self.symbols
        self.summary["start_date"] = str(self.start_date)
        self.summary["end_date"] = str(self.end_date)
        duration_days = max((self.end_date - self.start_date).days + 1, 1)
        trading_days = len(self.session_dates)
        self.summary["duration_days"] = duration_days
        self.summary["trading_days"] = trading_days
        avg_entry_credit = sum(self.entry_credit_history) / len(self.entry_credit_history) if self.entry_credit_history else 0.0
        max_entry_credit = max(self.entry_credit_history) if self.entry_credit_history else 0.0
        self.summary["avg_entry_credit"] = avg_entry_credit
        self.summary["max_entry_credit"] = max_entry_credit
        capital_base = max(avg_entry_credit, 1e-9)
        if self.pnl_events:
            sorted_events = sorted(self.pnl_events, key=lambda x: x["date"])
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
            self.summary["net_pnl"] = equity
            self.summary["max_drawdown"] = max_drawdown
            self.summary["trades_realized"] = len(pnl_values)
            self.summary["win_rate"] = (len(wins) / len(pnl_values)) if pnl_values else 0.0
            self.summary["avg_win"] = (sum(wins) / len(wins)) if wins else 0.0
            self.summary["avg_loss"] = (sum(losses) / len(losses)) if losses else 0.0
            self.summary["pnl_std"] = pstdev(pnl_values) if len(pnl_values) > 1 else 0.0
            annualized_factor = 365.0 / duration_days
            self.summary["annualized_return"] = (equity / capital_base) * annualized_factor if self.entry_credit_history else None
            returns_series = [p / capital_base for p in pnl_values] if self.entry_credit_history else []
            if len(returns_series) > 1:
                mean_return = mean(returns_series)
                std_return = pstdev(returns_series)
                if std_return > 0:
                    self.summary["sharpe_ratio"] = (math.sqrt(252.0) * mean_return) / std_return
                else:
                    self.summary["sharpe_ratio"] = None
            else:
                self.summary["sharpe_ratio"] = None
            self.summary["max_drawdown_pct"] = (max_drawdown / capital_base) if self.entry_credit_history else None
        else:
            self.summary["max_drawdown"] = 0.0
            self.summary["trades_realized"] = 0
            self.summary["win_rate"] = 0.0
            self.summary["avg_win"] = 0.0
            self.summary["avg_loss"] = 0.0
            self.summary["pnl_std"] = 0.0
            self.summary["annualized_return"] = None
            self.summary["sharpe_ratio"] = None
            self.summary["max_drawdown_pct"] = None

        logger.info(
            "Backtest execution finished",
            extra={"symbols": self.symbols, "net_pnl": self.summary.get("net_pnl"), "entries": self.summary["entries"], "exits": self.summary["exits"]},
        )

        return {
            "summary": self.summary,
            "decisions": self.decisions_rows,
            "trades": self.trades_records,
            "chains": self.chain_records,
            "pnl_events": self.pnl_events,
            "config": self.config,
        }


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


def _business_days(start_date: date, end_date: date) -> List[date]:
    if end_date < start_date:
        return []
    days: List[date] = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days
