from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from core.data.provider import LiquidityGate, MySQLProvider
from core.models.normalized import OptionRowNormalized
from datetime import date


@dataclass
class Candidate:
    kind: str  # 'CSP' | 'SPV'
    score: float
    info: Dict


def _contract_id(symbol: str, expiry: date | str, strike: float, right: str) -> str:
    if isinstance(expiry, date):
        expiry_str = expiry.strftime("%Y%m%d")
    else:
        expiry_str = str(expiry).replace("-", "")
    strike_str = ("%0.2f" % strike).rstrip("0").rstrip(".")
    return f"{symbol} {expiry_str} {strike_str} {right.upper()}"


def _filter_short_puts(rows: List[OptionRowNormalized], short_delta_range: Tuple[float, float]) -> List[OptionRowNormalized]:
    lo, hi = short_delta_range
    # For puts, delta is negative; use abs(delta)
    return [r for r in rows if r.right == 'put' and r.delta is not None and abs(r.delta) >= lo and abs(r.delta) <= hi]


def csp_candidates(
    provider: MySQLProvider,
    symbol: str,
    session_local_date,
    market: str,
    target_dte: Tuple[int, int],
    short_delta: Tuple[float, float],
    liquidity: LiquidityGate,
    top_k: int = 3,
) -> List[Candidate]:
    rows = provider.load_option_chain_daily(
        opt_symbol=symbol,
        session_local_date=session_local_date,
        market=market,
        dte_window=target_dte,
        liquidity=liquidity,
        delta_range=short_delta,
    )
    puts = _filter_short_puts(rows, short_delta)
    # score: prefer tighter spread, higher OI/volume, DTE near center
    scores: List[Tuple[float, OptionRowNormalized]] = []
    center = (target_dte[0] + target_dte[1]) / 2.0
    for r in puts:
        spread = max(0.0, r.ask - r.bid)
        spread_pen = spread / max(r.mark, 1e-6)
        dte_pen = abs(r.dte - center) / (center + 1e-6)
        liq_bonus = (r.open_interest or 0) / 1000.0 + (r.volume or 0) / 1000.0
        score = 1.0 - 0.5 * spread_pen - 0.3 * dte_pen + 0.2 * liq_bonus
        scores.append((score, r))
    scores.sort(key=lambda x: x[0], reverse=True)
    out: List[Candidate] = []
    for score, r in scores[:top_k]:
        out.append(
            Candidate(
                kind="CSP",
                score=float(score),
                info={
                    "target_id": r.target_id,
                    "symbol": r.opt_symbol,
                    "right": r.right,
                    "strike": float(r.strike_dec),
                    "expiry": str(r.expiry_local_date),
                    "dte": r.dte,
                    "mid": r.mid,
                    "mark": r.mark,
                    "delta": r.delta,
                    "oi": r.open_interest,
                    "volume": r.volume,
                    "multiplier": r.multiplier,
                    "contract_id": _contract_id(r.opt_symbol, r.expiry_local_date, float(r.strike_dec), r.right),
                },
            )
        )
    return out


def spv_candidates(
    provider: MySQLProvider,
    symbol: str,
    session_local_date,
    market: str,
    target_dte: Tuple[int, int],
    short_delta: Tuple[float, float],
    width_minmax: Tuple[int, int],
    min_credit_of_width: float,
    liquidity: LiquidityGate,
    top_k: int = 3,
) -> List[Candidate]:
    rows = provider.load_option_chain_daily(
        opt_symbol=symbol,
        session_local_date=session_local_date,
        market=market,
        dte_window=target_dte,
        liquidity=liquidity,
        delta_range=short_delta,
    )
    puts = _filter_short_puts(rows, short_delta)
    # Group by expiry, then for each short strike, find long strike lower by width range
    by_exp: Dict[tuple, List[OptionRowNormalized]] = {}
    for r in rows:
        by_exp.setdefault((r.expiry_local_date,), []).append(r)

    pairs: List[Tuple[float, dict]] = []
    for sp in puts:
        same_exp = [x for x in rows if x.expiry_local_date == sp.expiry_local_date and x.right == 'put']
        # build long candidates lower strikes
        for lp in same_exp:
            # ensure long strike < short strike
            if lp.strike_dec >= sp.strike_dec:
                continue
            width = float((sp.strike_dec - lp.strike_dec))
            if width < width_minmax[0] or width > width_minmax[1]:
                continue
            credit = sp.mid - lp.mid
            width_value = width  # by strike distance; currency will be width * multiplier
            # credit/width threshold on a per-strike basis approximates structure quality
            if width_value <= 0:
                continue
            if (credit / width_value) < min_credit_of_width:
                continue
            spread = max(0.0, (sp.ask - sp.bid) + (lp.ask - lp.bid))
            spread_pen = spread / max(sp.mark + lp.mark, 1e-6)
            liq_bonus = ((sp.open_interest or 0) + (lp.open_interest or 0)) / 1000.0
            score = credit / width_value - 0.2 * spread_pen + 0.1 * liq_bonus
            pairs.append((score, {
                "expiry": str(sp.expiry_local_date),
                "short_target_id": sp.target_id,
                "long_target_id": lp.target_id,
                "short_strike": float(sp.strike_dec),
                "long_strike": float(lp.strike_dec),
                "width": width,
                "dte": sp.dte,
                "credit": credit,
                "short_delta": sp.delta,
                "short_oi": sp.open_interest,
                "long_oi": lp.open_interest,
                "short_mid": sp.mid,
                "short_mark": sp.mark,
                "long_mid": lp.mid,
                "long_mark": lp.mark,
                "multiplier": sp.multiplier,
                "short_contract_id": _contract_id(sp.opt_symbol, sp.expiry_local_date, float(sp.strike_dec), sp.right),
                "long_contract_id": _contract_id(lp.opt_symbol, lp.expiry_local_date, float(lp.strike_dec), lp.right),
            }))

    pairs.sort(key=lambda x: x[0], reverse=True)
    out: List[Candidate] = []
    for score, info in pairs[:top_k]:
        out.append(Candidate(kind="SPV", score=float(score), info=info))
    return out
