import math
from datetime import date, datetime

import pytest

from app.screener import pricing
from app.screener.pricing import IST

# Reference values: Hull, S=K=100, T=1, r=5%, sigma=20% -> call 10.4506, put 5.5735
R = 0.05


def test_bs_reference_values():
    assert pricing.bs_price(100, 100, 1, 0.2, "CE", R) == pytest.approx(10.4506, abs=1e-3)
    assert pricing.bs_price(100, 100, 1, 0.2, "PE", R) == pytest.approx(5.5735, abs=1e-3)


def test_greeks_reference_values():
    g = pricing.greeks(100, 100, 1, 0.2, "CE", R)
    assert g.delta == pytest.approx(0.6368, abs=1e-3)
    assert g.gamma == pytest.approx(0.01876, abs=1e-4)
    assert g.vega == pytest.approx(0.3752, abs=1e-2)  # per 1% vol


def test_iv_roundtrip():
    price = pricing.bs_price(21800, 21900, 7 / 365, 0.15, "CE")
    assert pricing.implied_volatility(price, 21800, 21900, 7 / 365, "CE") == pytest.approx(0.15, abs=1e-4)


@pytest.mark.parametrize("spot,strike", [(0, 100), (-1, 100), (100, 0), (100, -5), (float("nan"), 100), (100, float("inf"))])
def test_invalid_spot_or_strike_rejected(spot, strike):
    for fn in (
        lambda: pricing.bs_price(spot, strike, 1, 0.2, "CE"),
        lambda: pricing.greeks(spot, strike, 1, 0.2, "PE"),
        lambda: pricing.implied_volatility(10, spot, strike, 1, "CE"),
    ):
        with pytest.raises(ValueError):
            fn()


def test_expired_option_is_intrinsic():
    assert pricing.bs_price(110, 100, 0, 0.2, "CE") == 10
    assert pricing.bs_price(90, 100, 0, 0.2, "CE") == 0


def test_iv_rank_and_percentile():
    hist = [0.10, 0.20, 0.30]
    assert pricing.iv_rank(0.25, hist) == 75.0
    assert pricing.iv_rank(0.25, []) == 0.0
    assert pricing.iv_rank(0.25, [0.2, 0.2]) == 0.0  # flat history: no division by zero
    assert pricing.iv_percentile(0.25, hist) == pytest.approx(66.67)


def test_time_to_expiry_uses_market_close():
    expiry = date(2026, 10, 8)
    five_pm = datetime(2026, 10, 1, 15, 30, tzinfo=IST)
    assert pricing.time_to_expiry_years(expiry, five_pm) == pytest.approx(7 / 365)
    # Same-day expiry mid-session is a fraction of a day, not a full day.
    morning = datetime(2026, 10, 8, 9, 30, tzinfo=IST)
    assert pricing.time_to_expiry_years(expiry, morning) == pytest.approx(6 / 24 / 365)
    # After the close it's expired.
    assert pricing.time_to_expiry_years(expiry, datetime(2026, 10, 8, 16, 0, tzinfo=IST)) == 0.0
    assert math.isfinite(pricing.MIN_T_YEARS)
