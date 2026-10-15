"""Rejected-signal capture: every engine gate records *why* it dropped a
candidate plus the decision inputs that were computable at that point."""

from app.models.schemas import StrategyType
from app.screener import engine
from app.screener.engine import ScreenerParams, run_screener_with_rejects
from tests.unit.test_engine import chain


def _rejects(snap, params=None):
    return run_screener_with_rejects(snap, params or ScreenerParams(min_iv_rank=0)).rejected


def by_strategy(rejs, t):
    return next(r for r in rejs if r.strategy_type == t)


def _fingerprint(signals):
    return [(s.strategy_type.value, tuple((l.strike, l.option_type.value, l.action.value) for l in s.legs), s.risk_reward.pop) for s in signals]


def test_run_screener_wrapper_matches_full_result():
    snap = chain()
    params = ScreenerParams(min_iv_rank=0)
    result = run_screener_with_rejects(snap, params)
    # uuids/generated_at differ between two runs, so compare a stable projection.
    assert _fingerprint(engine.run_screener(snap, params)) == _fingerprint(result.accepted)


def test_no_short_strike_is_recorded():
    snap = chain()
    snap.chain = [r for r in snap.chain if r.option_type == "CE"]  # no puts
    r = by_strategy(_rejects(snap), StrategyType.bull_put_spread)
    assert r.reject_reason == "no_short_strike"
    assert r.decision["computed"]["short_strike"] is None
    assert r.decision["params"]["spread_width"] == 100.0


def test_no_long_strike_is_recorded():
    snap = chain()
    snap.chain = [r for r in snap.chain if r.strike != 21600]  # the put long wing
    r = by_strategy(_rejects(snap), StrategyType.bull_put_spread)
    assert r.reject_reason == "no_long_strike"
    assert r.decision["computed"]["short_strike"] == 21700


def test_iv_rank_below_min_is_recorded_with_rank():
    r = by_strategy(_rejects(chain(), ScreenerParams(min_iv_rank=99)), StrategyType.bull_put_spread)
    assert r.reject_reason == "iv_rank_below_min"
    assert r.decision["computed"]["iv_rank"] is not None


def test_pop_below_min_is_recorded():
    r = by_strategy(_rejects(chain(), ScreenerParams(min_iv_rank=0, min_pop=1.0)), StrategyType.bull_put_spread)
    assert r.reject_reason == "pop_below_min"
    assert r.decision["computed"]["pop"] < 1.0


def test_credit_non_positive_is_recorded():
    snap = chain()
    for row in snap.chain:
        if row.strike == 21600:
            row.ltp = 90  # long wing dearer than the short leg -> negative credit
    r = by_strategy(_rejects(snap), StrategyType.bull_put_spread)
    assert r.reject_reason == "credit_non_positive"
    assert r.decision["computed"]["credit"] <= 0


def test_wing_missing_is_recorded_for_iron_condor():
    snap = chain()
    snap.chain = [r for r in snap.chain if r.option_type == "CE"]  # put wing missing
    r = by_strategy(_rejects(snap), StrategyType.iron_condor)
    assert r.reject_reason == "wing_missing"
    assert "put" in r.decision["computed"]["detail"]


def test_decision_json_has_complete_key_set():
    snap = chain()
    params = ScreenerParams(min_iv_rank=0)
    accepted = run_screener_with_rejects(snap, params).accepted
    assert accepted
    expected = {
        "iv_rank", "rsi", "support", "resistance", "short_strike", "short_delta", "short_iv",
        "pop", "credit", "max_loss", "breakeven", "breakeven_lower",
        "net_delta", "net_theta", "net_vega", "net_gamma",
    }
    for s in accepted:
        d = engine.build_decision(s, snap, params)
        assert expected <= set(d["computed"].keys())
        assert d["reject_reason"] is None
        assert set(d["params"].keys()) == {"min_iv_rank", "min_pop", "min_short_delta", "max_short_delta", "spread_width"}


def test_invariant_failure_is_captured_as_reject(monkeypatch):
    from app.screener import invariants

    monkeypatch.setattr(invariants, "check_signal", lambda s: ["pop must be within [0, 1]"])
    result = run_screener_with_rejects(chain(), ScreenerParams(min_iv_rank=0))
    assert result.accepted == []
    assert result.rejected and all(r.reject_reason == "invariant_failed" for r in result.rejected)
    assert any("pop must be within" in r.decision["computed"].get("detail", "") for r in result.rejected)
