from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy import text, create_engine
from sqlalchemy.engine import Engine

from core.models.normalized import OptionRowNormalized
from core.utils.timezone import (
    tz_for_market,
    local_midnight_to_utc_ms,
    utc_ms_to_local_date,
    month_buckets_between,
    add_days,
)


@dataclass
class LiquidityGate:
    min_oi: int = 500
    min_volume: int = 100
    max_spread_pct: float = 0.08


@dataclass
class DataProviderConfig:
    mysql_stock_dsn: str
    mysql_option_qs_us_dsn: str
    mysql_option_qs_hk_dsn: str
    mysql_option_history_dsn: str


class MySQLProvider:
    def __init__(self, cfg: DataProviderConfig):
        # Split DSNs by logical DBs
        self._eng_stock: Engine = create_engine(cfg.mysql_stock_dsn, pool_pre_ping=True)
        # Option quote/stat are per-market instances
        self._eng_opt_qs_us: Engine = create_engine(cfg.mysql_option_qs_us_dsn, pool_pre_ping=True)
        self._eng_opt_qs_hk: Engine = create_engine(cfg.mysql_option_qs_hk_dsn, pool_pre_ping=True)
        # Option history (shared instance for US/HK)
        self._eng_opt_hist: Engine = create_engine(cfg.mysql_option_history_dsn, pool_pre_ping=True)

    def connectivity(self) -> Dict[str, Dict[str, Any]]:
        """Test connectivity to all configured engines.
        Returns a mapping of engine name -> {ok: bool, error?: str}
        """
        out: Dict[str, Dict[str, Any]] = {}
        engines: Dict[str, Engine] = {
            "stock": self._eng_stock,
            "option_qs_us": self._eng_opt_qs_us,
            "option_qs_hk": self._eng_opt_qs_hk,
            "option_history": self._eng_opt_hist,
        }
        for name, eng in engines.items():
            try:
                with eng.connect() as conn:
                    conn.execute(text("SELECT 1"))
                out[name] = {"ok": True}
            except Exception as e:
                out[name] = {"ok": False, "error": str(e)}
        return out

    @staticmethod
    def _schema_for_option_history(market: str) -> str:
        return "us_option_history" if market.upper() == "US" else "hk_option_history"

    @staticmethod
    def _schema_for_stock_eod(market: str) -> str:
        return "stock_trade" if market.upper() == "US" else "hkstock_trade"

    def infer_month_tables(self, start_local: date, end_local: date, market: str) -> List[str]:
        months = month_buckets_between(start_local, end_local)
        schema = self._schema_for_option_history(market)
        return [f"{schema}.option_history_quote_{m}" for m in months]

    def load_underlying_daily(self, symbol: str, start_local: date, end_local: date, market: str) -> pd.DataFrame:
        schema = self._schema_for_stock_eod(market)
        table = f"{schema}.stock_quote_day"
        # timestamps in this table are seconds-level TIMESTAMP at local 00:00 (per data dict)
        sql = text(
            f"""
            SELECT symbol, timestamp, `open`, `high`, `low`, `close`, volume
            FROM {table}
            WHERE symbol = :symbol AND timestamp >= :t0 AND timestamp < :t1
            ORDER BY timestamp ASC
            """
        )
        tz = tz_for_market(market)
        t0_ms = local_midnight_to_utc_ms(start_local, tz)
        t1_ms = local_midnight_to_utc_ms(add_days(end_local, 1), tz)
        # Convert to seconds (TIMESTAMP seconds)
        params = {"symbol": symbol, "t0": t0_ms // 1000, "t1": t1_ms // 1000}
        with self._eng_stock.connect() as conn:
            df = pd.read_sql(sql, conn, params=params)
        if df.empty:
            return df
        # timestamp may be returned as seconds (int) or datetime; normalize to local date
        def _to_local_date(val):
            import pandas as _pd
            from datetime import datetime as _dt, timezone as _tz
            if isinstance(val, (int, float)):
                return utc_ms_to_local_date(int(val) * 1000, tz)
            if isinstance(val, _dt):
                # treat as UTC naive -> UTC; then to local
                if val.tzinfo is None:
                    val = val.replace(tzinfo=_tz.utc)
                ms = int(val.timestamp() * 1000)
                return utc_ms_to_local_date(ms, tz)
            # Fallback: let pandas parse
            try:
                ts = _pd.to_datetime(val, utc=True)
                ms = int(ts.value // 1_000_000)
                return utc_ms_to_local_date(ms, tz)
            except Exception:
                return None
        df["date_local"] = df["timestamp"].apply(_to_local_date)
        return df

    def load_hk_option_meta(self, symbols: Sequence[str]) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame(columns=["symbol", "ul", "multiplier", "min_tick"])
        # For simplicity and reliability with MySQL, query one-by-one for small lists
        frames: List[pd.DataFrame] = []
        with self._eng_opt_qs_hk.connect() as conn:
            for s in symbols:
                sql = text(
                    """
                    SELECT symbol, ul, multiplier, min_tick
                    FROM hkoption_quote.option_basic
                    WHERE symbol = :symbol
                    """
                )
                df = pd.read_sql(sql, conn, params={"symbol": s})
                frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["symbol", "ul", "multiplier", "min_tick"])

    def load_option_chain_daily(
        self,
        opt_symbol: str,
        session_local_date: date,
        market: str,
        dte_window: Tuple[int, int],
        liquidity: Optional[LiquidityGate] = None,
        delta_range: Optional[Tuple[float, float]] = None,
    ) -> List[OptionRowNormalized]:
        tz = tz_for_market(market)
        t0_ms = local_midnight_to_utc_ms(session_local_date, tz)
        t1_ms = local_midnight_to_utc_ms(add_days(session_local_date, 1), tz)
        e0_ms = local_midnight_to_utc_ms(add_days(session_local_date, dte_window[0]), tz)
        e1_ms = local_midnight_to_utc_ms(add_days(session_local_date, dte_window[1] + 1), tz)

        # Determine monthly tables between session_date and e1 bound (safe superset)
        months = month_buckets_between(session_local_date, utc_ms_to_local_date(e1_ms, tz))
        schema = self._schema_for_option_history(market)
        tables = [f"{schema}.option_history_quote_{m}" for m in months]
        if not tables:
            return []

        clauses = []
        params: Dict[str, object] = {
            "symbol": opt_symbol,
            "t0": t0_ms,
            "t1": t1_ms,
            "e0": e0_ms,
            "e1": e1_ms,
        }
        base_where = (
            "symbol = :symbol AND timestamp >= :t0 AND timestamp < :t1 "
            "AND expire_date >= :e0 AND expire_date < :e1"
        )
        if delta_range is not None:
            base_where += " AND ABS(delta) BETWEEN :dl AND :dh"
            params["dl"], params["dh"] = float(delta_range[0]), float(delta_range[1])
        if liquidity is not None:
            base_where += " AND open_int >= :min_oi AND volume >= :min_vol"
            params["min_oi"] = int(liquidity.min_oi)
            params["min_vol"] = int(liquidity.min_volume)

        select_cols = (
            "target_id, symbol, expire_date, strike, `call` AS call_flag, timestamp, "
            "bid_price, ask_price, midPrice, markPrice, implied_vol, delta, gamma, theta, vega, volume, open_int"
        )
        for t in tables:
            clauses.append(
                f"SELECT {select_cols} FROM {t} WHERE {base_where}"
            )
        union_sql = " UNION ALL ".join(clauses)
        outer = text(
            f"""
            SELECT * FROM (
              {union_sql}
            ) x
            ORDER BY expire_date ASC, strike ASC
            """
        )
        with self._eng_opt_hist.connect() as conn:
            df = pd.read_sql(outer, conn, params=params)

        if df.empty:
            return []

        # Normalize/derive columns
        rows: List[OptionRowNormalized] = []
        # HK multiplier/min_tick join if HK
        multiplier_map: Dict[str, Tuple[int, Optional[float]]] = {}
        if market.upper() == "HK":
            meta = self.load_hk_option_meta([opt_symbol])
            if not meta.empty:
                rec = meta.iloc[0]
                multiplier_map[opt_symbol] = (int(rec.get("multiplier") or 1), float(rec.get("min_tick")) if rec.get("min_tick") is not None else None)

        for _, r in df.iterrows():
            bid = float(r.get("bid_price") or 0.0)
            ask = float(r.get("ask_price") or 0.0)
            mid = r.get("midPrice")
            if mid is None or pd.isna(mid):
                if bid > 0 and ask > 0 and ask >= bid:
                    mid = (bid + ask) / 2.0
                else:
                    # fallback: use non-zero among bid/ask
                    mid = max(bid, ask)
            mark = r.get("markPrice")
            if mark is None or pd.isna(mark):
                mark = mid

            # Basic sanity filters; skip absurd quotes
            if bid < 0 or ask < 0 or mid <= 0 or mark <= 0:
                continue
            if ask and bid and ask < bid:
                # Harmonize slightly inverted quotes by snapping
                bid = min(bid, ask)
                ask = max(bid, ask)
                mid = (bid + ask) / 2.0

            call_flag = r.get("call_flag") if "call_flag" in r else r.get("call")
            right = "call" if int(call_flag) == 1 else "put"
            strike_dec = Decimal(str(r.get("strike")))
            session_d = utc_ms_to_local_date(int(r.get("timestamp")), tz)
            expiry_d = utc_ms_to_local_date(int(r.get("expire_date")), tz)
            dte = (expiry_d - session_d).days

            iv = r.get("implied_vol")
            delta = r.get("delta")
            gamma = r.get("gamma")
            theta = r.get("theta")
            vega = r.get("vega")
            volume = r.get("volume")
            oi = r.get("open_int")

            if iv is not None and (pd.isna(iv) or iv <= 0):
                iv = None
            if delta is not None and pd.isna(delta):
                delta = None
            # Liquidity guard on spread pct if provided
            if liquidity is not None and mark:
                spread = max(0.0, float(ask) - float(bid))
                if mark > 0 and spread / float(mark) > liquidity.max_spread_pct:
                    continue

            multiplier = 100 if market.upper() == "US" else multiplier_map.get(opt_symbol, (100, None))[0]
            min_tick = None if market.upper() == "US" else multiplier_map.get(opt_symbol, (100, None))[1]

            rows.append(
                OptionRowNormalized(
                    opt_symbol=str(r.get("symbol")),
                    target_id=int(r.get("target_id")),
                    right=right,
                    strike_dec=strike_dec,
                    expiry_local_date=expiry_d,
                    session_local_date=session_d,
                    bid=float(bid),
                    ask=float(ask),
                    mid=float(mid),
                    mark=float(mark),
                    iv=float(iv) if iv is not None else None,
                    delta=float(delta) if delta is not None else None,
                    gamma=float(gamma) if gamma is not None else None,
                    theta=float(theta) if theta is not None else None,
                    vega=float(vega) if vega is not None else None,
                    volume=int(volume) if volume is not None else None,
                    open_interest=int(oi) if oi is not None else None,
                    multiplier=int(multiplier),
                    min_tick=float(min_tick) if min_tick is not None else None,
                    dte=int(dte),
                )
            )

        return rows

    def load_leg_quotes(
        self,
        target_ids: Sequence[int],
        session_local_date: date,
        market: str,
        target_expiries: Optional[Dict[int, date]] = None,
    ) -> Dict[int, OptionRowNormalized]:
        if not target_ids:
            return {}
        tz = tz_for_market(market)
        t0_ms = local_midnight_to_utc_ms(session_local_date, tz)
        t1_ms = local_midnight_to_utc_ms(add_days(session_local_date, 1), tz)

        # target_ids -> comma separated string (ensuring ints)
        ids_clean = sorted({int(tid) for tid in target_ids})
        ids_str = ",".join(str(tid) for tid in ids_clean)
        if not ids_str:
            return {}

        months: List[str] = []
        if target_expiries:
            month_set = set()
            for exp in target_expiries.values():
                month_set.update(month_buckets_between(exp, exp))
            months = sorted(month_set)
        if not months:
            months = month_buckets_between(session_local_date, session_local_date)
        schema = self._schema_for_option_history(market)
        tables = [f"{schema}.option_history_quote_{m}" for m in months]
        if not tables:
            return {}

        select_cols = (
            "target_id, symbol, expire_date, strike, `call` AS call_flag, timestamp, "
            "bid_price, ask_price, midPrice, markPrice, implied_vol, delta, gamma, theta, vega, volume, open_int"
        )
        clauses = [
            f"SELECT {select_cols} FROM {t} WHERE target_id IN ({ids_str}) AND timestamp >= :t0 AND timestamp < :t1"
            for t in tables
        ]
        union_sql = " UNION ALL ".join(clauses)
        outer = text(
            f"""
            SELECT * FROM (
              {union_sql}
            ) leg_quotes
            ORDER BY timestamp DESC
            """
        )
        params = {"t0": t0_ms, "t1": t1_ms}

        with self._eng_opt_hist.connect() as conn:
            df = pd.read_sql(outer, conn, params=params)

        if df.empty:
            return {}

        result: Dict[int, OptionRowNormalized] = {}
        hk_meta: Dict[str, Tuple[int, Optional[float]]] = {}
        if market.upper() == "HK":
            symbols = df["symbol"].unique().tolist()
            meta_df = self.load_hk_option_meta(symbols)
            for _, rec in meta_df.iterrows():
                hk_meta[str(rec["symbol"])] = (
                    int(rec.get("multiplier") or 1),
                    float(rec.get("min_tick")) if rec.get("min_tick") is not None else None,
                )
        for _, r in df.iterrows():
            tid = int(r.get("target_id"))
            if tid in result:
                # already have latest quote
                continue
            bid = float(r.get("bid_price") or 0.0)
            ask = float(r.get("ask_price") or 0.0)
            mid = r.get("midPrice")
            if mid is None or pd.isna(mid):
                if bid > 0 and ask > 0 and ask >= bid:
                    mid = (bid + ask) / 2.0
                else:
                    mid = max(bid, ask)
            mark = r.get("markPrice")
            if mark is None or pd.isna(mark):
                mark = mid
            if bid < 0 or ask < 0 or mid <= 0 or mark <= 0:
                continue
            if ask and bid and ask < bid:
                bid = min(bid, ask)
                ask = max(bid, ask)
                mid = (bid + ask) / 2.0

            expiry_d = utc_ms_to_local_date(int(r.get("expire_date")), tz)
            session_d = utc_ms_to_local_date(int(r.get("timestamp")), tz)
            multiplier = 100
            min_tick = None
            if market.upper() == "HK":
                multiplier, min_tick = hk_meta.get(str(r.get("symbol")), (100, None))

            result[tid] = OptionRowNormalized(
                opt_symbol=str(r.get("symbol")),
                target_id=tid,
                right="call" if int(r.get("call_flag")) == 1 else "put",
                strike_dec=Decimal(str(r.get("strike"))),
                expiry_local_date=expiry_d,
                session_local_date=session_d,
                bid=float(bid),
                ask=float(ask),
                mid=float(mid),
                mark=float(mark),
                iv=float(r.get("implied_vol")) if r.get("implied_vol") and not pd.isna(r.get("implied_vol")) else None,
                delta=float(r.get("delta")) if r.get("delta") and not pd.isna(r.get("delta")) else None,
                gamma=float(r.get("gamma")) if r.get("gamma") and not pd.isna(r.get("gamma")) else None,
                theta=float(r.get("theta")) if r.get("theta") and not pd.isna(r.get("theta")) else None,
                vega=float(r.get("vega")) if r.get("vega") and not pd.isna(r.get("vega")) else None,
                volume=int(r.get("volume")) if r.get("volume") is not None else None,
                open_interest=int(r.get("open_int")) if r.get("open_int") is not None else None,
                multiplier=int(multiplier),
                min_tick=min_tick,
                dte=(expiry_d - session_d).days,
            )
        return result
