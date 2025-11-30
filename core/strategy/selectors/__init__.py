from __future__ import annotations

from core.strategy.registry import SelectorSpec, registry
from core.strategy.selectors.puts import fetch_csp, fetch_spv
from core.strategy.selectors.calls import fetch_scv, fetch_lcv
from core.strategy.selectors.condor import ic_candidates

# Register built-in selectors
registry.register("CSP", SelectorSpec(kind="CSP", fetch=fetch_csp))
registry.register("SPV", SelectorSpec(kind="SPV", fetch=fetch_spv))
registry.register("SCV", SelectorSpec(kind="SCV", fetch=fetch_scv))
registry.register("LCV", SelectorSpec(kind="LCV", fetch=fetch_lcv))
registry.register(
    "IC",
    SelectorSpec(
        kind="IC",
        fetch=lambda provider, symbol, session_date, market, target_dte, delta_range, width_range, min_cow, max_debit, gate, top_k: ic_candidates(
            provider,
            symbol,
            session_date,
            market,
            target_dte,
            delta_range,
            width_range,
            min_cow,
            gate,
            top_k=top_k,
        ),
    ),
)
