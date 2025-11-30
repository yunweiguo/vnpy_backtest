from __future__ import annotations

from typing import List, Tuple

from core.data.provider import LiquidityGate, MySQLProvider
from core.strategy.selector_csp_spv import _filter_short_puts, _filter_short_calls, Candidate, _contract_id


def ic_candidates(
    provider: MySQLProvider,
    symbol: str,
    session_date,
    market: str,
    target_dte: Tuple[int, int],
    short_delta: Tuple[float, float],
    width_range: Tuple[int, int],
    min_credit_of_width: float,
    liquidity: LiquidityGate,
    top_k: int = 3,
) -> List[Candidate]:
    rows = provider.load_option_chain_daily(
        opt_symbol=symbol,
        session_local_date=session_date,
        market=market,
        dte_window=target_dte,
        liquidity=liquidity,
        delta_range=short_delta,
    )

    puts = _filter_short_puts(rows, short_delta)
    calls = _filter_short_calls(rows, short_delta)
    pairs: List[Tuple[float, dict]] = []

    for sp in puts:
        for sc in calls:
            # same expiry only
            if sp.expiry_local_date != sc.expiry_local_date:
                continue
            same_exp_puts = [r for r in rows if r.expiry_local_date == sp.expiry_local_date and r.right == 'put']
            same_exp_calls = [r for r in rows if r.expiry_local_date == sc.expiry_local_date and r.right == 'call']
            for lp in same_exp_puts:
                if lp.strike_dec >= sp.strike_dec:
                    continue
                put_width = float(sp.strike_dec - lp.strike_dec)
                if put_width < width_range[0] or put_width > width_range[1]:
                    continue
                for lc in same_exp_calls:
                    if lc.strike_dec <= sc.strike_dec:
                        continue
                    call_width = float(lc.strike_dec - sc.strike_dec)
                    if call_width < width_range[0] or call_width > width_range[1]:
                        continue
                    width = max(put_width, call_width)
                    credit = (sp.mid - lp.mid) + (sc.mid - lc.mid)
                    if width <= 0 or credit <= 0:
                        continue
                    credit_ratio = credit / width
                    if credit_ratio < min_credit_of_width:
                        continue
                    spread_pen = max(0.0, (sp.ask - sp.bid) + (lp.ask - lp.bid) + (sc.ask - sc.bid) + (lc.ask - lc.bid))
                    spread_pen /= max(sp.mark + lp.mark + sc.mark + lc.mark, 1e-6)
                    liq_bonus = sum((leg.open_interest or 0) for leg in [sp, lp, sc, lc]) / 4000.0
                    score = credit_ratio - 0.2 * spread_pen + 0.1 * liq_bonus
                    pairs.append((score, {
                        "expiry": str(sp.expiry_local_date),
                        "short_put_id": sp.target_id,
                        "long_put_id": lp.target_id,
                        "short_call_id": sc.target_id,
                        "long_call_id": lc.target_id,
                        "short_put_strike": float(sp.strike_dec),
                        "long_put_strike": float(lp.strike_dec),
                        "short_call_strike": float(sc.strike_dec),
                        "long_call_strike": float(lc.strike_dec),
                        "put_width": put_width,
                        "call_width": call_width,
                        "width": width,
                        "dte": sp.dte,
                        "credit": credit,
                        "short_put_delta": sp.delta,
                        "short_call_delta": sc.delta,
                        "multiplier": sp.multiplier,
                        # quotes
                        "short_put_mid": sp.mid,
                        "short_put_mark": sp.mark,
                        "short_put_bid": sp.bid,
                        "short_put_ask": sp.ask,
                        "short_put_oi": sp.open_interest,
                        "short_put_volume": sp.volume,
                        "long_put_mid": lp.mid,
                        "long_put_mark": lp.mark,
                        "long_put_bid": lp.bid,
                        "long_put_ask": lp.ask,
                        "long_put_oi": lp.open_interest,
                        "long_put_volume": lp.volume,
                        "short_call_mid": sc.mid,
                        "short_call_mark": sc.mark,
                        "short_call_bid": sc.bid,
                        "short_call_ask": sc.ask,
                        "short_call_oi": sc.open_interest,
                        "short_call_volume": sc.volume,
                        "long_call_mid": lc.mid,
                        "long_call_mark": lc.mark,
                        "long_call_bid": lc.bid,
                        "long_call_ask": lc.ask,
                        "long_call_oi": lc.open_interest,
                        "long_call_volume": lc.volume,
                        "short_put_contract_id": _contract_id(sp.opt_symbol, sp.expiry_local_date, float(sp.strike_dec), sp.right),
                        "long_put_contract_id": _contract_id(lp.opt_symbol, lp.expiry_local_date, float(lp.strike_dec), lp.right),
                        "short_call_contract_id": _contract_id(sc.opt_symbol, sc.expiry_local_date, float(sc.strike_dec), sc.right),
                        "long_call_contract_id": _contract_id(lc.opt_symbol, lc.expiry_local_date, float(lc.strike_dec), lc.right),
                    }))

    pairs.sort(key=lambda x: x[0], reverse=True)
    out: List[Candidate] = []
    for score, info in pairs[:top_k]:
        out.append(Candidate(kind="IC", score=float(score), info=info))
    return out
