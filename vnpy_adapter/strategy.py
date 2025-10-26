from __future__ import annotations

import json
from typing import Dict, Any

from vnpy_ctastrategy.template import CtaTemplate
from vnpy.trader.object import BarData

from api.main import ConfigModel, normalize_config
from settings import load_settings
from core.logging_config import configure_logging, get_logger
from worker.backtest_core import execute_backtest


class OptionRollStrategy(CtaTemplate):
    author = "options-backtest"
    parameters = ["config_json"]
    variables: list[str] = []

    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str, setting: Dict[str, Any]) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.config_json: str = setting.get("config_json", "{}")
        self.runtime_result: Dict[str, Any] | None = None
        configure_logging(load_settings().log_level, load_settings().log_file, load_settings().log_console)
        self.logger = get_logger(__name__)

    def on_init(self) -> None:
        self.write_log("OptionRollStrategy init")

    def on_start(self) -> None:
        self.write_log("OptionRollStrategy start")
        try:
            raw = json.loads(self.config_json)
            cfg = ConfigModel.model_validate(raw)
            normalized = normalize_config(cfg)
            settings = load_settings()
            self.runtime_result = execute_backtest(normalized, settings)
            summary = self.runtime_result.get("summary", {})
            self.write_log(f"vnpy backtest completed, net_pnl={summary.get('net_pnl')}")
        except Exception as exc:
            self.write_log(f"vnpy backtest failed: {exc}")
            raise

    def on_stop(self) -> None:
        self.write_log("OptionRollStrategy stop")

    def on_bar(self, bar: BarData) -> None:
        pass

    def on_tick(self, tick) -> None:  # pragma: no cover - not used
        pass

    def on_order(self, order) -> None:  # pragma: no cover - not used
        pass

    def on_trade(self, trade) -> None:  # pragma: no cover - not used
        pass
