"""Black-Scholes option pricing, Greeks, and implied volatility for index options."""

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from scipy.optimize import brentq
from scipy.stats import norm

RISK_FREE_RATE = 0.065  # approx. Indian T-bill rate; move to config if it needs tuning


@dataclass
class Greeks:
    delta: float
    gamma: float
    theta: float
    vega: float


def _require_valid(spot: float, strike: float) -> None:
    """Non-positive or non-finite spot/strike make log(spot/strike) undefined
    (or silently NaN) — fail loudly instead of returning garbage prices."""
    if not (math.isfinite(spot) and math.isfinite(strike) and spot > 0 and strike > 0):
        raise ValueError(f"spot and strike must be finite and > 0 (got spot={spot!r}, strike={strike!r})")


def _d1_d2(spot: float, strike: float, t_years: float, iv: float, r: float) -> Tuple[float, float]:
    _require_valid(spot, strike)
    if not (t_years > 0 and iv > 0 and math.isfinite(t_years) and math.isfinite(iv)):
        raise ValueError("t_years and iv must be finite and > 0")
    d1 = (math.log(spot / strike) + (r + 0.5 * iv**2) * t_years) / (iv * math.sqrt(t_years))
    d2 = d1 - iv * math.sqrt(t_years)
    return d1, d2


def bs_price(
    spot: float, strike: float, t_years: float, iv: float, option_type: str, r: float = RISK_FREE_RATE
) -> float:
    _require_valid(spot, strike)
    if t_years <= 0 or iv <= 0:
        intrinsic = max(spot - strike, 0) if option_type == "CE" else max(strike - spot, 0)
        return intrinsic

    d1, d2 = _d1_d2(spot, strike, t_years, iv, r)
    if option_type == "CE":
        return spot * norm.cdf(d1) - strike * math.exp(-r * t_years) * norm.cdf(d2)
    return strike * math.exp(-r * t_years) * norm.cdf(-d2) - spot * norm.cdf(-d1)


def implied_volatility(
    market_price: float, spot: float, strike: float, t_years: float, option_type: str, r: float = RISK_FREE_RATE
) -> float:
    """Solve for IV via Brent's method against the observed option premium."""
    _require_valid(spot, strike)
    if t_years <= 0 or not math.isfinite(market_price) or market_price <= 0:
        return 0.0

    def f(iv: float) -> float:
        return bs_price(spot, strike, t_years, iv, option_type, r) - market_price

    try:
        return brentq(f, 1e-4, 5.0, xtol=1e-6)
    except ValueError:
        return 0.0


def greeks(spot: float, strike: float, t_years: float, iv: float, option_type: str, r: float = RISK_FREE_RATE) -> Greeks:
    _require_valid(spot, strike)
    if t_years <= 0 or iv <= 0:
        return Greeks(delta=0.0, gamma=0.0, theta=0.0, vega=0.0)

    d1, d2 = _d1_d2(spot, strike, t_years, iv, r)
    pdf_d1 = norm.pdf(d1)

    delta = norm.cdf(d1) if option_type == "CE" else norm.cdf(d1) - 1
    gamma = pdf_d1 / (spot * iv * math.sqrt(t_years))
    vega = spot * pdf_d1 * math.sqrt(t_years) / 100  # per 1% IV move

    if option_type == "CE":
        theta = (
            -spot * pdf_d1 * iv / (2 * math.sqrt(t_years))
            - r * strike * math.exp(-r * t_years) * norm.cdf(d2)
        ) / 365
    else:
        theta = (
            -spot * pdf_d1 * iv / (2 * math.sqrt(t_years))
            + r * strike * math.exp(-r * t_years) * norm.cdf(-d2)
        ) / 365

    return Greeks(delta=round(delta, 4), gamma=round(gamma, 5), theta=round(theta, 2), vega=round(vega, 2))


def probability_of_profit_credit_spread(short_delta: float) -> float:
    """Approximation: POP for a short credit spread ~ 1 - |delta of short leg|."""
    return round(1 - abs(short_delta), 4)


def iv_rank(current_iv: float, historical_iv: List[float]) -> float:
    if not historical_iv:
        return 0.0
    lo, hi = min(historical_iv), max(historical_iv)
    if hi == lo:
        return 0.0
    return round((current_iv - lo) / (hi - lo) * 100, 2)


def iv_percentile(current_iv: float, historical_iv: List[float]) -> float:
    if not historical_iv:
        return 0.0
    below = sum(1 for v in historical_iv if v <= current_iv)
    return round(below / len(historical_iv) * 100, 2)


IST = ZoneInfo("Asia/Kolkata")
MARKET_CLOSE = time(15, 30)
# Don't recommend opening credit spreads inside the final hour to expiry:
# gamma explodes and the Black-Scholes inputs are least reliable.
MIN_T_YEARS = 1 / (365 * 24)


def time_to_expiry_years(expiry: date, now: Optional[datetime] = None) -> float:
    """Calendar time to the 15:30 IST expiry-day close, in years. Returns 0.0
    once expired. (The old max(days, 1)/365 treated same-day expiry as a full
    extra day, overpricing time value.)"""
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    expiry_dt = datetime.combine(expiry, MARKET_CLOSE, tzinfo=IST)
    seconds = (expiry_dt - now).total_seconds()
    return max(seconds, 0.0) / (365 * 24 * 3600)
