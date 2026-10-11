"""Signal invariants — written once, used twice.

Runtime guardrail: engine.run_screener() drops any signal that violates
these and logs why, so a math bug can never surface as a tradeable-looking
recommendation. Eval: backend/evals/ scores the same checks over golden and
recorded chains.
"""

import math
from typing import List

from app.models.schemas import DataSource, ScreenerSignal, StrategyType

_EXPECTED_LEGS = {
    StrategyType.bull_put_spread: 2,
    StrategyType.bear_call_spread: 2,
    StrategyType.iron_condor: 4,
}


def check_signal(signal: ScreenerSignal) -> List[str]:
    """Returns a list of human-readable violations (empty means valid)."""
    problems: List[str] = []
    rr = signal.risk_reward

    for name, value in (("max_profit", rr.max_profit), ("max_loss", rr.max_loss), ("pop", rr.pop), ("breakeven", rr.breakeven)):
        if not math.isfinite(value):
            problems.append(f"{name} is not finite")
    if problems:
        return problems

    if rr.max_profit <= 0:
        problems.append("max_profit must be > 0 for a credit strategy")
    if rr.max_loss <= 0:
        problems.append("max_loss must be > 0")
    if not 0.0 <= rr.pop <= 1.0:
        problems.append("pop must be within [0, 1]")
    if signal.data_source not in set(DataSource):
        problems.append("data_source missing or unknown")

    expected = _EXPECTED_LEGS.get(signal.strategy_type)
    if expected is not None and len(signal.legs) != expected:
        problems.append(f"{signal.strategy_type.value} must have {expected} legs, got {len(signal.legs)}")

    for leg in signal.legs:
        if not (math.isfinite(leg.strike) and leg.strike > 0):
            problems.append("leg strike must be finite and > 0")
        if leg.price is not None and not (math.isfinite(leg.price) and leg.price > 0):
            problems.append("leg price must be finite and > 0")

    shorts = sorted(leg.strike for leg in signal.legs if leg.action.value == "SELL")
    if shorts and signal.strategy_type == StrategyType.bull_put_spread and not rr.breakeven < max(shorts):
        problems.append("bull put breakeven must sit below the short strike")
    if shorts and signal.strategy_type == StrategyType.bear_call_spread and not rr.breakeven > min(shorts):
        problems.append("bear call breakeven must sit above the short strike")

    if signal.strategy_type == StrategyType.iron_condor:
        lower = rr.breakeven_lower
        if lower is None or not math.isfinite(lower) or not lower < rr.breakeven:
            problems.append("iron condor needs breakeven_lower < breakeven (upper)")
        elif len(shorts) == 2 and not (lower < shorts[0] and rr.breakeven > shorts[1]):
            problems.append("iron condor breakevens must sit outside the short strikes")

    return problems
