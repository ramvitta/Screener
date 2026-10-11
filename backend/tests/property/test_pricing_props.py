import math

from hypothesis import given, settings
from hypothesis import strategies as st

from app.screener import pricing

spot = st.floats(min_value=1000, max_value=50000)
moneyness = st.floats(min_value=0.8, max_value=1.2)
t = st.floats(min_value=1 / 365, max_value=1.0)
vol = st.floats(min_value=0.05, max_value=1.0)
rate = st.floats(min_value=0.0, max_value=0.1)


@settings(max_examples=150, deadline=None)
@given(spot, moneyness, t, vol, rate)
def test_put_call_parity(s, m, t_, v, r):
    k = s * m
    call = pricing.bs_price(s, k, t_, v, "CE", r)
    put = pricing.bs_price(s, k, t_, v, "PE", r)
    assert math.isclose(call - put, s - k * math.exp(-r * t_), rel_tol=1e-6, abs_tol=1e-6 * s)


@settings(max_examples=150, deadline=None)
@given(spot, moneyness, t, vol)
def test_delta_bounds_and_sign(s, m, t_, v):
    k = s * m
    assert 0.0 <= pricing.greeks(s, k, t_, v, "CE").delta <= 1.0
    assert -1.0 <= pricing.greeks(s, k, t_, v, "PE").delta <= 0.0


@settings(max_examples=100, deadline=None)
@given(spot, moneyness, t, vol, st.floats(min_value=0.01, max_value=0.2))
def test_price_monotonic_in_vol(s, m, t_, v, dv):
    k = s * m
    for opt in ("CE", "PE"):
        assert pricing.bs_price(s, k, t_, v + dv, opt) >= pricing.bs_price(s, k, t_, v, opt) - 1e-9


@settings(max_examples=100, deadline=None)
@given(spot, st.floats(min_value=0.9, max_value=1.1), st.floats(min_value=7 / 365, max_value=0.5), st.floats(min_value=0.1, max_value=0.8))
def test_iv_roundtrip(s, m, t_, v):
    k = s * m
    price = pricing.bs_price(s, k, t_, v, "CE")
    # Vol is only identifiable from the *time value*: skip contracts that are
    # all intrinsic (deep ITM) or all-but-worthless (deep OTM).
    time_value = price - max(s - k * math.exp(-pricing.RISK_FREE_RATE * t_), 0.0)
    if time_value < 1.0:
        return
    assert math.isclose(pricing.implied_volatility(price, s, k, t_, "CE"), v, rel_tol=1e-2, abs_tol=2e-3)
