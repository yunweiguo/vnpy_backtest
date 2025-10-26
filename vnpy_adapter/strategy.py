from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict, List, Optional

from vnpy_ctastrategy.template import CtaTemplate
from vnpy.trader.object import BarData

from api.main import ConfigModel, normalize_config
from settings import load_settings
from core.logging_config import configure_logging, get_logger
from core.strategy.runtime import StrategyRuntime


class OptionRollStrategy(CtaTemplate):
    author = "options-backtest"
    parameters = ["config_json"]
    variables: list[str] = []

    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str, setting: Dict[str, Any]) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.config_json: str = setting.get("config_json", "{}")
        self.runtime_result: Dict[str, Any] | None = None
        self.settings = load_settings()
        configure_logging(self.settings.log_level, self.settings.log_file, self.settings.log_console)
        self.logger = get_logger(__name__)
        self.runtime: Optional[StrategyRuntime] = None
        self._session_dates: List[date] = []
        self._session_index: int = 0
        self._finalized: bool = False

    def on_init(self) -> None:
        self.write_log("OptionRollStrategy init")

    def on_start(self) -> None:
        self.write_log("OptionRollStrategy start")
        try:
            raw = json.loads(self.config_json)
            cfg = ConfigModel.model_validate(raw)
            normalized = normalize_config(cfg)
            self.runtime = StrategyRuntime(normalized, self.settings)
            self._session_dates = list(self.runtime.session_dates)
            self._session_index = 0
            if not self._session_dates:
                self.write_log("无可用交易日，直接完成")
                self._finalize_runtime()
        except Exception as exc:
            self.write_log(f"vnpy backtest failed: {exc}")
            raise

    def on_stop(self) -> None:
        self.write_log("OptionRollStrategy stop")
        self._finalize_runtime()

    def on_bar(self, bar: BarData) -> None:
        if not self.runtime or self._finalized:
            return

        session_date = bar.datetime.date()
        if self._session_index >= len(self._session_dates):
            self._finalize_runtime()
            return

        # Process all sessions up to the current bar date (inclusive)
        try:
            while self._session_index < len(self._session_dates) and self._session_dates[self._session_index] <= session_date:
                current_date = self._session_dates[self._session_index]
                self.runtime.process_session(current_date)
                self._session_index += 1
        except Exception as exc:
            self.write_log(f"处理交易日 {session_date} 失败: {exc}")
            raise

        if self._session_index >= len(self._session_dates):
            self._finalize_runtime()

    def on_tick(self, tick) -> None:  # pragma: no cover - not used
        pass

    def on_order(self, order) -> None:  # pragma: no cover - not used
        pass

    def on_trade(self, trade) -> None:  # pragma: no cover - not used
        pass

    def _finalize_runtime(self) -> None:
        if not self.runtime or self._finalized:
            return
        try:
            self.runtime_result = self.runtime.finalize()
            summary = self.runtime_result.get("summary", {})
            self.write_log(f"vnpy backtest completed, net_pnl={summary.get('net_pnl')}")
        except Exception as exc:
            self.write_log(f"vnpy backtest finalize failed: {exc}")
            self._finalized = True
            raise
        else:
            self._finalized = True
