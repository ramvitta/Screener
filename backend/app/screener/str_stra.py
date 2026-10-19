"""
str-stra strategy port — the pure-computation core of the three `str-stra/`
scripts (unified_screener.py, str_stra_screener.py, candle_adx_screener.py),
reimplemented without pandas so it runs inside the FastAPI backend next to the
existing credit-spread engine.

Three passes over a single symbol's daily OHLCV + NFO live quotes:

  PASS 1  ADX        — ADX falling (adx_change < 0) AND ADX Trend == "Neutral"
  PASS 2  Candle     — Supply candle (close < open) AND volume below 20-day avg
                       → comment "Inaction"
  PASS 3  Straddle   — |CE - PE| / midpoint <= ATM_PARITY_PCT (2%)

Combined tiers: PRIME (3 passes), READY (2), WATCH (1). Every indicator here
is pure (list-in/list-out) so it is unit-testable without any network call.
"""

from typing import List, Optional, Tuple

ADX_PERIOD = 14
ADX_TREND_WINDOW = 3
AVG_PERIOD = 20

# (symbol, lot_size, strike_step) — the NSE F&O watchlist from the str-stra
# scripts. Lot sizes are the July-2026 NFO values; verify periodically at
# nseindia.com/option-chain as the exchange revises them.
WATCHLIST: List[Tuple[str, int, float]] = [
    ("ABB", 125, 25.0),
    ("ADANIENT", 250, 5.0),
    ("ADANIPORTS", 1250, 2.5),
    ("ADANIPOWER", 2000, 2.5),
    ("ANGELONE", 250, 10.0),
    ("ASIANPAINT", 200, 10.0),
    ("AXISBANK", 1200, 5.0),
    ("BEL", 3500, 1.0),
    ("BHARTIARTL", 500, 5.0),
    ("BHEL", 4500, 1.0),
    ("BIOCON", 2500, 2.0),
    ("BSE", 375, 10.0),
    ("COALINDIA", 2100, 2.0),
    ("COLPAL", 700, 5.0),
    ("DIXON", 100, 50.0),
    ("DMART", 175, 10.0),
    ("ETERNAL", 4200, 1.0),
    ("HAL", 150, 25.0),
    ("HDFCAMC", 300, 10.0),
    ("HDFCBANK", 550, 5.0),
    ("HDFCLIFE", 1100, 5.0),
    ("HINDALCO", 1000, 2.5),
    ("HINDUNILVR", 300, 10.0),
    ("ICICIBANK", 700, 5.0),
    ("INFY", 400, 10.0),
    ("ITC", 3200, 1.0),
    ("JIOFIN", 6000, 1.0),
    ("KALYANKJIL", 2500, 2.5),
    ("LT", 150, 10.0),
    ("MARUTI", 75, 100.0),
    ("MAZDOCK", 500, 5.0),
    ("NTPC", 4500, 1.0),
    ("PFC", 3000, 2.5),
    ("POLICYBZR", 940, 5.0),
    ("POLYCAB", 175, 10.0),
    ("POWERGRID", 4500, 1.0),
    ("RELIANCE", 250, 10.0),
    ("RVNL", 7000, 1.0),
    ("SBIN", 1500, 1.0),
    ("SIEMENS", 275, 25.0),
    ("SUNPHARMA", 350, 5.0),
    ("SUZLON", 14000, 0.5),
    ("TCS", 150, 25.0),
    ("TMPV", 700, 5.0),
    ("TRENT", 375, 10.0),
    ("UNITDSPR", 1000, 5.0),
    ("WAAREEENER", 275, 10.0),
    ("WIPRO", 1500, 2.0),
    ("YESBANK", 45000, 0.1),
]


def wilder(values: List[Optional[float]], period: int) -> List[Optional[float]]:
    """Wilder's smoothing (RMA). Seeds with the mean of the first `period`
    non-None values; None inputs propagate as None (so the tail is only valid
    where the inputs are valid). Mirrors the str-stra `_wilder`/`wilder_smooth`."""
    n = len(values)
    out: List[Optional[float]] = [None] * n
    valid = [v is not None for v in values]
    start = period - 1
    while start < n and not all(valid[start - period + 1 : start + 1]):
        start += 1
    if start >= n:
        return out
    seed_vals = [values[i] for i in range(start - period + 1, start + 1) if values[i] is not None]
    seed = sum(seed_vals) / len(seed_vals)
    out[start] = seed
    prev = seed
    for i in range(start + 1, n):
        if values[i] is None:
            out[i] = None
        else:
            prev = (prev * (period - 1) + values[i]) / period
            out[i] = prev
    return out


def adx(highs: List[float], lows: List[float], closes: List[float], period: int = ADX_PERIOD) -> Tuple[List[Optional[float]], List[Optional[float]], List[Optional[float]]]:
    """Compute +DI, -DI, ADX (Wilder) — returns three lists aligned to `closes`."""
    n = len(closes)
    tr: List[float] = [0.0] * n
    plus_dm: List[float] = [0.0] * n
    minus_dm: List[float] = [0.0] * n
    for i in range(n):
        prev_close = closes[i - 1] if i > 0 else closes[i]
        prev_high = highs[i - 1] if i > 0 else highs[i]
        prev_low = lows[i - 1] if i > 0 else lows[i]
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - prev_close), abs(lows[i] - prev_close))
        up = highs[i] - prev_high
        down = prev_low - lows[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    atr = wilder(tr, period)
    pdm_s = wilder(plus_dm, period)
    mdm_s = wilder(minus_dm, period)

    plus_di: List[Optional[float]] = [None] * n
    minus_di: List[Optional[float]] = [None] * n
    dx: List[Optional[float]] = [None] * n
    for i in range(n):
        if atr[i]:
            plus_di[i] = 100 * pdm_s[i] / atr[i]  # type: ignore[operator]
            minus_di[i] = 100 * mdm_s[i] / atr[i]  # type: ignore[operator]
            s = plus_di[i] + minus_di[i]  # type: ignore[operator]
            dx[i] = 100 * abs(plus_di[i] - minus_di[i]) / s if s else None  # type: ignore[operator]

    adx_series = wilder(dx, period)
    return plus_di, minus_di, adx_series


def adx_trend_label(adx_series: List[Optional[float]], window: int = ADX_TREND_WINDOW) -> str:
    """Zoneweb-style ADX Trend: consistently rising → "Trending", consistently
    falling → "Weakening", otherwise "Neutral"."""
    vals = [v for v in adx_series if v is not None]
    if len(vals) < 2:
        return "Neutral"
    recent = vals[-window:]
    if len(recent) < 2:
        return "Neutral"
    diffs = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
    if all(d > 0 for d in diffs):
        return "Trending"
    if all(d < 0 for d in diffs):
        return "Weakening"
    return "Neutral"


def _roll_mean(vals: List[float], period: int) -> float:
    if not vals:
        return 0.0
    window = vals[-period:] if len(vals) >= period else vals
    return sum(window) / len(window)


def candle_indicators(
    opens: List[float], highs: List[float], lows: List[float], closes: List[float], volumes: List[float], avg_period: int = AVG_PERIOD
) -> dict:
    """Last-bar candle/volume classification (Supply/Demand + "Inaction")."""
    n = len(closes)
    bodies = [closes[i] - opens[i] for i in range(n)]
    ranges = [highs[i] - lows[i] for i in range(n)]
    avg_body = _roll_mean([abs(b) for b in bodies], avg_period)
    avg_vol = _roll_mean(volumes, avg_period)

    last = n - 1
    body = bodies[last]
    rng = ranges[last]
    candle_type = "Supply" if body < 0 else "Demand"
    strength = abs(body) / rng * 100 if rng else 0.0
    size = abs(body) / avg_body * 100 if avg_body else 0.0
    vol_pct = (volumes[last] - avg_vol) / avg_vol * 100 if avg_vol else 0.0

    if candle_type == "Supply" and vol_pct < 0:
        comment = "Inaction"
    elif candle_type == "Supply":
        comment = "Action"
    elif vol_pct < 0:
        comment = "Quiet Demand"
    else:
        comment = "Active Demand"

    return {
        "candle_type": candle_type,
        "candle_strength": round(strength, 2),
        "candle_size": round(size, 2),
        "volume_pct": round(vol_pct, 2),
        "comment": comment,
    }


def _comment_for(opens: List[float], closes: List[float], volumes: List[float], avg_period: int) -> str:
    """The per-bar comment (Inaction/Action/Quiet Demand/Active Demand) using
    only body sign + volume-vs-average — no high/low needed."""
    body = closes[-1] - opens[-1]
    avg_vol = _roll_mean(volumes, avg_period)
    vol_pct = (volumes[-1] - avg_vol) / avg_vol * 100 if avg_vol else 0.0
    supply = body < 0
    if supply and vol_pct < 0:
        return "Inaction"
    if supply:
        return "Action"
    return "Quiet Demand" if vol_pct < 0 else "Active Demand"


def three_candle_pattern(closes: List[float], opens: List[float], volumes: List[float], avg_period: int = AVG_PERIOD) -> str:
    """Descriptive label for the last three candles (matches the str-stra
    `three_candle_pattern`/`check_three_candle_pattern`)."""
    if len(closes) < 3:
        return "—"
    n = len(closes)
    comments = [
        _comment_for(opens[: i + 1], closes[: i + 1], volumes[: i + 1], avg_period)
        for i in range(n - 3, n)
    ]
    cur = comments[-1]
    if cur == "Inaction":
        prior = ["Supply" if closes[i] < opens[i] else "Demand" for i in range(n - 3, n - 1)]
        return "Inaction after Demand" if "Demand" in prior else "Consecutive Inaction"
    cur_type = "Supply" if closes[-1] < opens[-1] else "Demand"
    return f"{cur_type} / {cur}"


def snap(price: float, step: float) -> float:
    """Snap a price to the nearest strike step, avoiding binary float drift
    (e.g. YESBANK's 0.1 step producing 24.400000000000002)."""
    if step <= 0:
        return price
    return round(round(price / step) * step, 6)


def technical_metrics(candles: List[dict]) -> Optional[dict]:
    """Pass 1 + Pass 2 combined: ADX + candle indicators from daily OHLCV.

    Returns a dict of every ADX/candle field (plus `adx_pass`/`candle_pass`),
    or None when there isn't enough history for a stable ADX-14."""
    if len(candles) < ADX_PERIOD + AVG_PERIOD:
        return None

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    opens = [c["open"] for c in candles]
    volumes = [c["volume"] for c in candles]

    plus_di, minus_di, adx_series = adx(highs, lows, closes)
    last = len(closes) - 1
    adx_now = adx_series[last]
    adx_prev = adx_series[last - 1] if last > 0 else None
    if adx_now is None or adx_prev is None or adx_prev == 0:
        return None

    adx_chg = (adx_now - adx_prev) / adx_prev * 100
    trend = adx_trend_label(adx_series)
    pdi = plus_di[last]
    mdi = minus_di[last]
    if pdi is None or mdi is None:
        return None
    dmi_trend = "Positive" if pdi > mdi else "Negative"
    dmi_diff = abs(pdi - mdi) / (mdi if mdi > 0 else 1) * 100

    cndl = candle_indicators(opens, highs, lows, closes, volumes)

    adx_pass = adx_chg < 0 and trend == "Neutral"
    candle_pass = cndl["comment"] == "Inaction"

    lcp = closes[last]
    pct_chg = (closes[last] - closes[last - 1]) / closes[last - 1] * 100 if last > 0 else 0.0

    return {
        "lcp": round(lcp, 2),
        "pct_chg": round(pct_chg, 2),
        "adx": round(adx_now, 2),
        "plus_di": round(pdi, 2),
        "minus_di": round(mdi, 2),
        "adx_chg_pct": round(adx_chg, 2),
        "dmi_diff_pct": round(dmi_diff, 2),
        "dmi_trend": dmi_trend,
        "adx_trend": trend,
        "adx_pass": adx_pass,
        "candle_type": cndl["candle_type"],
        "candle_strength": cndl["candle_strength"],
        "candle_size": cndl["candle_size"],
        "volume_pct": cndl["volume_pct"],
        "comment": cndl["comment"],
        "pattern": three_candle_pattern(closes, opens, volumes),
        "candle_pass": candle_pass,
    }


def straddle_metrics(quotes: dict, step: float, lot: int, parity_pct: float) -> dict:
    """Pass 3: ATM CE/PE parity from live NFO quotes.

    `quotes` carries the fetched values (fut_ltp, ce, pe, otm_ce, otm_pe) — the
    worker fetches them so this stays pure. Returns parity fields + the
    straddle/strangle premiums and the `straddle_pass` flag."""
    fut = quotes.get("fut_ltp")
    ce = quotes.get("ce")
    pe = quotes.get("pe")
    empty = {
        "fut_ltp": fut,
        "atm": snap(fut, step) if fut else None,
        "ce_ltp": ce,
        "pe_ltp": pe,
        "spread_pct": None,
        "straddle_prem": None,
        "straddle_val": None,
        "strangle_prem": None,
        "strangle_val": None,
        "straddle_pass": False,
    }
    if not fut or not ce or not pe:
        return empty

    atm = snap(fut, step)
    mid = (ce + pe) / 2
    spread_pct = abs(ce - pe) / mid * 100 if mid > 0 else 999.0

    otm_ce = quotes.get("otm_ce")
    otm_pe = quotes.get("otm_pe")
    straddle_prem = ce + pe
    strangle_prem = (otm_ce or 0) + (otm_pe or 0)

    return {
        "fut_ltp": round(fut, 2),
        "atm": atm,
        "ce_ltp": round(ce, 2),
        "pe_ltp": round(pe, 2),
        "spread_pct": round(spread_pct, 2),
        "straddle_prem": round(straddle_prem, 2),
        "straddle_val": round(straddle_prem * lot, 0),
        "otm_ce": otm_ce,
        "otm_pe": otm_pe,
        "strangle_prem": round(strangle_prem, 2),
        "strangle_val": round(strangle_prem * lot, 0),
        "straddle_pass": spread_pct <= parity_pct,
    }


def tier_of(adx_pass: bool, candle_pass: bool, straddle_pass: bool) -> Tuple[int, str]:
    n = sum([adx_pass, candle_pass, straddle_pass])
    if n == 3:
        return n, "PRIME"
    if n == 2:
        return n, "READY"
    if n == 1:
        return n, "WATCH"
    return n, "—"
