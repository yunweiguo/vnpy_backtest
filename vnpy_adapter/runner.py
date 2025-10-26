from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, Any

from vnpy_ctastrategy.backtesting import BacktestingEngine
from vnpy.trader.constant import Interval, Exchange
from vnpy.trader.object import BarData

from vnpy_adapter.strategy import OptionRollStrategy


def run_vnpy_backtest(config: Dict[str, Any]) -> Dict[str, Any]:
    config_json = json.dumps(config)

    engine = BacktestingEngine()
    start_dt = datetime.now()
    end_dt = start_dt

    engine.set_parameters(
        vt_symbol="DUMMY.SSE",
        interval=Interval.DAILY,
        start=start_dt,
        end=end_dt,
        rate=0,
        slippage=0,
        size=1,
        pricetick=0.01,
        capital=0,
    )

    dummy_bar = BarData(
        symbol="DUMMY",
        exchange=Exchange.SSE,
        datetime=start_dt,
        interval=Interval.DAILY,
        volume=0,
        turnover=0,
        open_price=0,
        high_price=0,
        low_price=0,
        close_price=0,
        gateway_name="BACKTEST",
    )
    engine.history_data = [dummy_bar]

    engine.add_strategy(OptionRollStrategy, {"config_json": config_json})
    engine.run_backtesting()

    strategy: OptionRollStrategy = engine.strategy
    if not strategy.runtime_result:
        raise RuntimeError("vn.py backtest did not produce results")
    return strategy.runtime_result
