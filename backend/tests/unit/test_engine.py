from datetime import date, timedelta

import pytest

from app.models.schemas import DataSource, StrategyType
from app.screener import engine, invariants
from app.screener.engine import ChainRow, OptionChainSnapshot, ScreenerParams


def chain(**over):
    rows = [
        ChainRow(21600, "PE", 14, 0.16, -0.10),
        ChainRow(21700, "PE", 27, 0.152, -0.19),
        ChainRow(21800, "PE", 45, 0.148, -0.25),
        ChainRow(22000, "CE", 45, 0.141, 0.20),
        ChainRow(22100, "CE", 23, 0.136, 0.11),
        ChainRow(22200, "CE", 10, 0.128, 0.05),
    ]
    snap = dict(
        symbol="NIFTY",
        expiry=date.today() + timedelta(days=5),
        spot=21834.5,
        t_years=5 / 365,
        chain=rows,
        underlying_closes=[21700 + i * 3 for i in range(60)],
        historical_iv=[0.11 + 0.001 * i for i in range(120)],
        data_source=DataSource.live,
    )
    snap.update(over)
    return OptionChainSnapshot(**snap)


def by_type(signals, t):
    return next(s for s in signals if s.strategy_type == t)


def test_credit_spreads_generated_and_labelled():
    signals = engine.run_screener(chain(), ScreenerParams(min_iv_rank=0))
    put = by_type(signals, StrategyType.bull_put_spread)
    assert put.data_source == DataSource.live
    assert put.risk_reward.max_profit == pytest.approx(put.legs[0].price - put.legs[1].price)
    assert put.risk_reward.max_loss == pytest.approx(100 - put.risk_reward.max_profit)
    assert put.risk_reward.breakeven == pytest.approx(put.legs[0].strike - put.risk_reward.max_profit)


def test_iron_condor_pop_is_joint_probability_not_min():
    """Regression: the old formula p1*p2/max(p1,p2) == min(p1,p2)."""
    signals = engine.run_screener(chain(), ScreenerParams(min_iv_rank=0))
    put, call, ic = (by_type(signals, t) for t in (StrategyType.bull_put_spread, StrategyType.bear_call_spread, StrategyType.iron_condor))
    expected = put.risk_reward.pop + call.risk_reward.pop - 1
    assert ic.risk_reward.pop == pytest.approx(expected, abs=1e-4)
    assert ic.risk_reward.pop < min(put.risk_reward.pop, call.risk_reward.pop)


def test_iron_condor_has_both_breakevens_and_true_max_loss():
    signals = engine.run_screener(chain(), ScreenerParams(min_iv_rank=0))
    put, call, ic = (by_type(signals, t) for t in (StrategyType.bull_put_spread, StrategyType.bear_call_spread, StrategyType.iron_condor))
    total_credit = put.risk_reward.max_profit + call.risk_reward.max_profit
    assert ic.risk_reward.max_profit == pytest.approx(total_credit)
    assert ic.risk_reward.max_loss == pytest.approx(100 - total_credit)
    assert ic.risk_reward.breakeven_lower == pytest.approx(put.legs[0].strike - total_credit)
    assert ic.risk_reward.breakeven == pytest.approx(call.legs[0].strike + total_credit)


def test_all_emitted_signals_satisfy_invariants():
    for s in engine.run_screener(chain(), ScreenerParams(min_iv_rank=0)):
        assert invariants.check_signal(s) == []


def test_missing_long_leg_yields_no_signal():
    snap = chain()
    snap.chain = [r for r in snap.chain if r.strike != 21700]  # the put wing
    assert not [s for s in engine.run_screener(snap, ScreenerParams(min_iv_rank=0)) if s.strategy_type == StrategyType.bull_put_spread]


def test_no_iv_history_skips_filter_and_says_so():
    signals = engine.run_screener(chain(historical_iv=[]), ScreenerParams(min_iv_rank=90))
    assert signals, "IV rank can't be evaluated without history, so it must not silently reject"
    assert all("IV Rank unavailable" in s.reason for s in signals)


def test_real_iv_history_filter_still_applies():
    assert not engine.run_screener(chain(historical_iv=[0.10 + 0.002 * i for i in range(120)]), ScreenerParams(min_iv_rank=99))


def test_crossed_market_signal_dropped_by_guardrail(caplog):
    snap = chain()
    for r in snap.chain:
        if r.strike == 21600:
            r.ltp = 90  # long wing dearer than the short leg -> negative credit
    signals = engine.run_screener(snap, ScreenerParams(min_iv_rank=0))
    assert not [s for s in signals if s.strategy_type == StrategyType.bull_put_spread]


def test_invariants_catch_bad_signals():
    good = by_type(engine.run_screener(chain(), ScreenerParams(min_iv_rank=0)), StrategyType.bull_put_spread)
    assert invariants.check_signal(good.model_copy(update={"risk_reward": good.risk_reward.model_copy(update={"pop": 1.5})}))
    assert invariants.check_signal(good.model_copy(update={"risk_reward": good.risk_reward.model_copy(update={"max_loss": -1})}))
    assert invariants.check_signal(good.model_copy(update={"risk_reward": good.risk_reward.model_copy(update={"max_profit": float("nan")})}))
    assert invariants.check_signal(good.model_copy(update={"legs": good.legs[:1]}))
