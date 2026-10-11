"""
Phase 1 screener engine: Credit Spreads (Bull Put / Bear Call) and Iron Condor
candidate generation for NIFTY, per SRS_Architecture.md section 4.

Consumes an OptionChainSnapshot (built from ClickHouse candles + live quotes)
and produces ranked ScreenerSignal objects with a templated "Reason for Trade".
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import List, Optional

from app.models.schemas import DataSource, Greeks, Leg, LegAction, OptionType, RiskReward, ScreenerSignal, StrategyType
from app.screener import invariants, pricing, technicals

logger = logging.getLogger(__name__)

# IV rank needs a real trailing history; below this many observations it is
# reported as unavailable instead of being computed from a handful of points.
MIN_IV_HISTORY = 20


@dataclass
class ChainRow:
    strike: float
    option_type: str  # "CE" | "PE"
    ltp: float
    iv: float
    delta: float


@dataclass
class OptionChainSnapshot:
    symbol: str
    expiry: date
    spot: float
    t_years: float
    chain: List[ChainRow]
    underlying_closes: List[float] = field(default_factory=list)
    historical_iv: List[float] = field(default_factory=list)
    data_source: DataSource = DataSource.sample


@dataclass
class ScreenerParams:
    min_iv_rank: float = 40.0
    min_pop: float = 0.60
    min_short_delta: float = 0.10
    max_short_delta: float = 0.30
    spread_width: float = 100.0  # strike distance between short/long leg


def _find_strike(chain: List[ChainRow], option_type: str, target_delta: float) -> Optional[ChainRow]:
    candidates = [r for r in chain if r.option_type == option_type]
    if not candidates:
        return None
    return min(candidates, key=lambda r: abs(abs(r.delta) - target_delta))


def _find_by_strike(chain: List[ChainRow], strike: float, option_type: str) -> Optional[ChainRow]:
    for r in chain:
        if r.strike == strike and r.option_type == option_type:
            return r
    return None


def _iv_rank_or_none(current_iv: float, snapshot: "OptionChainSnapshot") -> Optional[float]:
    if len(snapshot.historical_iv) < MIN_IV_HISTORY:
        return None
    return pricing.iv_rank(current_iv, snapshot.historical_iv)


def _iv_rank_text(iv_rank: Optional[float]) -> str:
    return f"IV Rank at {iv_rank:.0f}%" if iv_rank is not None else "IV Rank unavailable (insufficient history)"


def _reason(strategy_label: str, iv_rank: Optional[float], rsi_value: float, pop: float, credit: float, max_loss: float, legs_desc: str) -> str:
    bias = technicals.technical_bias(rsi_value)
    return (
        f"{_iv_rank_text(iv_rank)} with Nifty RSI at {rsi_value:.0f} ({bias}). "
        f"{strategy_label} {legs_desc} offers {pop * 100:.0f}% POP with ₹{credit:.0f} credit "
        f"against ₹{max_loss:.0f} max loss."
    )


def generate_credit_spread_signals(
    snapshot: OptionChainSnapshot, strategy_type: StrategyType, params: ScreenerParams
) -> List[ScreenerSignal]:
    option_type = "PE" if strategy_type == StrategyType.bull_put_spread else "CE"
    short = _find_strike(snapshot.chain, option_type, (params.min_short_delta + params.max_short_delta) / 2)
    if short is None:
        return []

    long_strike = short.strike - params.spread_width if option_type == "PE" else short.strike + params.spread_width
    long_leg = _find_by_strike(snapshot.chain, long_strike, option_type)
    if long_leg is None:
        return []

    current_iv_rank = _iv_rank_or_none(short.iv, snapshot)
    # With no real IV history the filter can't be evaluated; it is skipped and
    # the signal's reason says so, rather than judging against fabricated data.
    if current_iv_rank is not None and current_iv_rank < params.min_iv_rank:
        return []

    credit = round((short.ltp - long_leg.ltp), 2)
    max_loss = round(params.spread_width - credit, 2)
    pop = pricing.probability_of_profit_credit_spread(short.delta)
    if pop < params.min_pop or credit <= 0:
        return []

    short_greeks = pricing.greeks(snapshot.spot, short.strike, snapshot.t_years, short.iv, option_type)
    long_greeks = pricing.greeks(snapshot.spot, long_leg.strike, snapshot.t_years, long_leg.iv, option_type)
    net_delta = round(
        (long_greeks.delta - short_greeks.delta) if option_type == "PE" else (short_greeks.delta - long_greeks.delta),
        2,
    )

    rsi_value = technicals.rsi(snapshot.underlying_closes)
    breakeven = short.strike - credit if option_type == "PE" else short.strike + credit
    strategy_label = "Bull Put Spread" if strategy_type == StrategyType.bull_put_spread else "Bear Call Spread"
    legs_desc = f"{int(long_leg.strike)}/{int(short.strike)}" if option_type == "PE" else f"{int(short.strike)}/{int(long_leg.strike)}"

    signal = ScreenerSignal(
        signal_id=str(uuid.uuid4()),
        strategy_type=strategy_type,
        symbol=snapshot.symbol,
        expiry=snapshot.expiry,
        legs=[
            Leg(strike=short.strike, option_type=OptionType(option_type), action=LegAction.SELL, delta=short.delta, iv=short.iv, price=short.ltp),
            Leg(strike=long_leg.strike, option_type=OptionType(option_type), action=LegAction.BUY, delta=long_leg.delta, iv=long_leg.iv, price=long_leg.ltp),
        ],
        greeks=Greeks(delta=net_delta, theta=round(short_greeks.theta - long_greeks.theta, 2), vega=round(short_greeks.vega - long_greeks.vega, 2), gamma=round(short_greeks.gamma - long_greeks.gamma, 4)),
        risk_reward=RiskReward(max_profit=credit, max_loss=max_loss, breakeven=round(breakeven, 2), pop=pop),
        reason=_reason(strategy_label, current_iv_rank, rsi_value, pop, credit, max_loss, legs_desc),
        generated_at=datetime.now(timezone.utc),
        data_source=snapshot.data_source,
    )
    return [signal]


def generate_iron_condor_signals(snapshot: OptionChainSnapshot, params: ScreenerParams) -> List[ScreenerSignal]:
    put_signals = generate_credit_spread_signals(snapshot, StrategyType.bull_put_spread, params)
    call_signals = generate_credit_spread_signals(snapshot, StrategyType.bear_call_spread, params)
    if not put_signals or not call_signals:
        return []

    put_leg, call_leg = put_signals[0], call_signals[0]
    # The two short strikes can't both finish in the money, so the loss events
    # are mutually exclusive: P(profit zone) = P(put OTM) + P(call OTM) - 1.
    # (The old p1*p2/max(p1,p2) simplified to min(p1, p2), which ignores the
    # other wing's risk entirely.)
    combined_pop = round(max(0.0, put_leg.risk_reward.pop + call_leg.risk_reward.pop - 1.0), 4)
    max_profit = round(put_leg.risk_reward.max_profit + call_leg.risk_reward.max_profit, 2)
    put_width = put_leg.legs[0].strike - put_leg.legs[1].strike
    call_width = call_leg.legs[1].strike - call_leg.legs[0].strike
    # Only one wing can lose at expiry, and both credits are kept.
    max_loss = round(max(put_width, call_width) - max_profit, 2)
    breakeven_lower = round(put_leg.legs[0].strike - max_profit, 2)
    breakeven_upper = round(call_leg.legs[0].strike + max_profit, 2)

    current_iv_rank = _iv_rank_or_none(put_leg.legs[0].iv, snapshot)
    support, resistance = technicals.support_resistance(snapshot.underlying_closes)

    legs_desc = (
        f"{int(put_leg.legs[1].strike)}/{int(put_leg.legs[0].strike)}-"
        f"{int(call_leg.legs[0].strike)}/{int(call_leg.legs[1].strike)}"
    )

    signal = ScreenerSignal(
        signal_id=str(uuid.uuid4()),
        strategy_type=StrategyType.iron_condor,
        symbol=snapshot.symbol,
        expiry=snapshot.expiry,
        legs=put_leg.legs + call_leg.legs,
        greeks=Greeks(
            delta=round(put_leg.greeks.delta + call_leg.greeks.delta, 2),
            theta=round(put_leg.greeks.theta + call_leg.greeks.theta, 2),
            vega=round(put_leg.greeks.vega + call_leg.greeks.vega, 2),
            gamma=round(put_leg.greeks.gamma + call_leg.greeks.gamma, 4),
        ),
        risk_reward=RiskReward(
            max_profit=max_profit,
            max_loss=max_loss,
            breakeven=breakeven_upper,
            breakeven_lower=breakeven_lower,
            pop=combined_pop,
        ),
        reason=(
            f"{_iv_rank_text(current_iv_rank)} with Nifty range-bound between key support "
            f"{support} and resistance {resistance}. Iron Condor {legs_desc} offers "
            f"{combined_pop * 100:.0f}% combined POP."
        ),
        generated_at=datetime.now(timezone.utc),
        data_source=snapshot.data_source,
    )
    return [signal]


def run_screener(snapshot: OptionChainSnapshot, params: Optional[ScreenerParams] = None) -> List[ScreenerSignal]:
    params = params or ScreenerParams()
    signals: List[ScreenerSignal] = []
    signals += generate_credit_spread_signals(snapshot, StrategyType.bull_put_spread, params)
    signals += generate_credit_spread_signals(snapshot, StrategyType.bear_call_spread, params)
    signals += generate_iron_condor_signals(snapshot, params)

    # Runtime guardrail: a signal that breaks an invariant is a math/data bug,
    # not a recommendation — drop it and say why.
    valid: List[ScreenerSignal] = []
    for signal in signals:
        problems = invariants.check_signal(signal)
        if problems:
            logger.error("Dropping invalid %s signal: %s", signal.strategy_type.value, "; ".join(problems))
            continue
        valid.append(signal)
    return sorted(valid, key=lambda s: s.risk_reward.pop, reverse=True)
