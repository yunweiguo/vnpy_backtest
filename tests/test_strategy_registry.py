from datetime import date
from decimal import Decimal

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.strategy.selectors  # noqa: F401 - populate registry via side effects
from core.data.provider import LiquidityGate
from core.models.normalized import OptionRowNormalized
from core.strategy.registry import registry


class FakeProvider:
    def __init__(self, rows):
        self._rows = rows

    def load_option_chain_daily(self, **_kwargs):
        return self._rows


def _make_row(opt_symbol: str, target_id: int, right: str, strike: float, expiry: date, dte: int, bid: float, ask: float, mid: float, delta: float, oi: int = 500, volume: int = 200) -> OptionRowNormalized:
    return OptionRowNormalized(
        opt_symbol=opt_symbol,
        target_id=target_id,
        right=right,
        strike_dec=Decimal(str(strike)),
        expiry_local_date=expiry,
        session_local_date=date(2024, 1, 2),
        bid=bid,
        ask=ask,
        mid=mid,
        mark=mid,
        iv=0.3,
        delta=delta,
        gamma=None,
        theta=None,
        vega=None,
        volume=volume,
        open_interest=oi,
        multiplier=100,
        min_tick=0.01,
        dte=dte,
    )


def test_registry_has_default_strategies():
    assert set(registry.kinds()) == {"CSP", "SPV", "SCV", "LCV", "IC"}


def test_scv_selector_builds_vertical():
    session = date(2024, 1, 2)
    expiry = date(2024, 2, 2)
    rows = [
        _make_row("AAPL", 1, "call", 100.0, expiry, 30, bid=1.9, ask=2.1, mid=2.0, delta=0.25),
        _make_row("AAPL", 2, "call", 102.0, expiry, 30, bid=0.9, ask=1.1, mid=1.0, delta=0.15),
    ]
    provider = FakeProvider(rows)
    gate = LiquidityGate(min_oi=0, min_volume=0, max_spread_pct=1.0)
    spec = registry.get("SCV")
    assert spec is not None

    cands = spec.fetch(
        provider,
        "AAPL",
        session,
        "US",
        (25, 35),
        (0.2, 0.3),
        (1, 5),
        0.3,
        0.55,
        gate,
        top_k=1,
    )
    assert cands, "expected SCV candidate"
    cand = cands[0]
    assert cand.kind == "SCV"
    assert cand.info["short_target_id"] == 1
    assert cand.info["long_target_id"] == 2
    assert cand.info["width"] == 2.0
    # credit = 2.0 - 1.0 = 1.0, width=2 -> credit/width = 0.5
    assert cand.info["credit"] == 1.0


def test_ic_selector_builds_condor():
    session = date(2024, 1, 2)
    expiry = date(2024, 2, 2)
    rows = [
        _make_row("AAPL", 1, "put", 95.0, expiry, 30, bid=1.9, ask=2.1, mid=2.0, delta=-0.2),
        _make_row("AAPL", 2, "put", 93.0, expiry, 30, bid=0.9, ask=1.1, mid=1.0, delta=-0.1),
        _make_row("AAPL", 3, "call", 105.0, expiry, 30, bid=1.8, ask=2.2, mid=2.0, delta=0.2),
        _make_row("AAPL", 4, "call", 107.0, expiry, 30, bid=0.8, ask=1.0, mid=0.9, delta=0.1),
    ]
    provider = FakeProvider(rows)
    gate = LiquidityGate(min_oi=0, min_volume=0, max_spread_pct=1.0)
    spec = registry.get("IC")
    assert spec is not None

    cands = spec.fetch(
        provider,
        "AAPL",
        session,
        "US",
        (25, 35),
        (0.1, 0.25),
        (1, 5),
        0.3,
        0.55,
        gate,
        top_k=1,
    )
    assert cands, "expected IC candidate"
    cand = cands[0]
    assert cand.kind == "IC"
    assert {cand.info["short_put_id"], cand.info["short_call_id"]} == {1, 3}
    assert {cand.info["long_put_id"], cand.info["long_call_id"]} == {2, 4}
    assert cand.info["credit"] == (2.0 - 1.0) + (2.0 - 0.9)
