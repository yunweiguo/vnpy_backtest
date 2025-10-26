from __future__ import annotations

import json
from datetime import datetime, time, date
from typing import Any, Dict, List, Optional

from vnpy_ctastrategy.backtesting import BacktestingEngine
from vnpy.trader.constant import Interval, Exchange
from vnpy.trader.object import BarData

from core.data.provider import DataProviderConfig, MySQLProvider
from core.strategy.runtime import StrategyRuntime
from settings import load_settings
from vnpy_adapter.strategy import OptionRollStrategy


def _resolve_underlying_symbol(provider: MySQLProvider, market: str, option_symbol: str) -> str:
    if market.upper() != "HK":
        return option_symbol
    meta = provider.load_hk_option_meta([option_symbol])
    if meta.empty:
        return option_symbol
    record = meta.iloc[0]
    ul = record.get("ul")
    return str(ul) if ul else option_symbol


def _build_history_bars(
    session_dates: List[date],
    config: Dict[str, Any],
    provider: Optional[MySQLProvider],
) -> List[BarData]:
    if not session_dates:
        now = datetime.now()
        return [
            BarData(
                symbol="DUMMY",
                exchange=Exchange.SSE,
                datetime=now,
                interval=Interval.DAILY,
                volume=0,
                turnover=0,
                open_price=0,
                high_price=0,
                low_price=0,
                close_price=0,
                gateway_name="BACKTEST",
            )
        ]

    price_map: Dict[date, Dict[str, float]] = {}
    strategy_cfg = config.get("strategy", {})
    market = strategy_cfg.get("market", "US")
    symbols = strategy_cfg.get("symbols") or []

    bar_symbol = "BACKTEST"
    if provider is not None and symbols:
        option_symbol = symbols[0]
        underlying_symbol = _resolve_underlying_symbol(provider, market, option_symbol)
        bar_symbol = underlying_symbol or bar_symbol
        df = provider.load_underlying_daily(underlying_symbol, session_dates[0], session_dates[-1], market)
        if not df.empty:
            for _, row in df.iterrows():
                raw_date = row.get("date_local")
                if raw_date is None:
                    continue
                if isinstance(raw_date, datetime):
                    date_key = raw_date.date()
                elif isinstance(raw_date, date):
                    date_key = raw_date
                else:
                    try:
                        date_key = datetime.fromisoformat(str(raw_date)).date()
                    except ValueError:
                        continue
                price_map[date_key] = {
                    "open": float(row.get("open") or 0.0),
                    "high": float(row.get("high") or 0.0),
                    "low": float(row.get("low") or 0.0),
                    "close": float(row.get("close") or 0.0),
                    "volume": float(row.get("volume") or 0.0),
                }

    bars: List[BarData] = []
    for session_date in session_dates:
        dt = datetime.combine(session_date, time.min)
        prices = price_map.get(session_date, {})
        bars.append(
            BarData(
                symbol=bar_symbol,
                exchange=Exchange.SSE,
                datetime=dt,
                interval=Interval.DAILY,
                volume=prices.get("volume", 0.0),
                turnover=0,
                open_price=prices.get("open", 0.0),
                high_price=prices.get("high", 0.0),
                low_price=prices.get("low", 0.0),
                close_price=prices.get("close", 0.0),
                gateway_name="BACKTEST",
            )
        )
    return bars


def run_vnpy_backtest(config: Dict[str, Any]) -> Dict[str, Any]:
    config_json = json.dumps(config)
    session_dates = StrategyRuntime.generate_session_dates(config)
    settings = load_settings()
    provider = MySQLProvider(
        DataProviderConfig(
            mysql_stock_dsn=settings.mysql_stock_dsn,
            mysql_option_qs_us_dsn=settings.mysql_option_qs_us_dsn,
            mysql_option_qs_hk_dsn=settings.mysql_option_qs_hk_dsn,
            mysql_option_history_dsn=settings.mysql_option_history_dsn,
        )
    )
    history_bars = _build_history_bars(session_dates, config, provider)

    engine = BacktestingEngine()

    start_dt = history_bars[0].datetime
    end_dt = history_bars[-1].datetime

    engine.set_parameters(
        vt_symbol="BACKTEST.SSE",
        interval=Interval.DAILY,
        start=start_dt,
        end=end_dt,
        rate=0,
        slippage=0,
        size=1,
        pricetick=0.01,
        capital=0,
    )

    engine.history_data = history_bars

    engine.add_strategy(OptionRollStrategy, {"config_json": config_json})
    engine.run_backtesting()

    strategy: OptionRollStrategy = engine.strategy
    if not strategy.runtime_result:
        raise RuntimeError("vn.py backtest did not produce results")
    return strategy.runtime_result
