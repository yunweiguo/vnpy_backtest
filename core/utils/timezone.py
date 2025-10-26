from __future__ import annotations

from datetime import datetime, date, timedelta, timezone
from typing import List

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore


def tz_for_market(market: str) -> str:
    if market.upper() == "US":
        return "America/New_York"
    if market.upper() == "HK":
        return "Asia/Hong_Kong"
    raise ValueError(f"Unknown market: {market}")


def local_midnight_to_utc_ms(d: date, tz_name: str) -> int:
    tz = ZoneInfo(tz_name)
    # Local midnight at the start of day
    local_dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)
    utc_dt = local_dt.astimezone(timezone.utc)
    return int(utc_dt.timestamp() * 1000)


def utc_ms_to_local_date(ms: int, tz_name: str) -> date:
    tz = ZoneInfo(tz_name)
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(tz)
    # normalize to local date
    return date(dt.year, dt.month, dt.day)


def month_buckets_between(start_d: date, end_d: date) -> List[str]:
    if end_d < start_d:
        return []
    months: List[str] = []
    cur = date(start_d.year, start_d.month, 1)
    last = date(end_d.year, end_d.month, 1)
    while cur <= last:
        months.append(f"{cur.year}{cur.month:02d}")
        # next month
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return months


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)

