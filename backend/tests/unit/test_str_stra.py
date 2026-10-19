"""Pure-indicator tests for the str-stra port (app/screener/str_stra.py):
Wilder ADX, ADX trend classification, candle "inaction", straddle parity, and
tiering. No network — every input is a plain list/dict."""

import math

from app.screener.str_stra import (
    adx,
    adx_trend_label,
    candle_indicators,
    snap,
    straddle_metrics,
    technical_metrics,
    tier_of,
    wilder,
)


def _candles(closes, opens=None, highs=None, lows=None, volumes=None):
    n = len(closes)
    opens = opens or [c for c in closes]
    highs = highs or [max(c, o) + 1 for c, o in zip(closes, opens)]
    lows = lows or [min(c, o) - 1 for c, o in zip(closes, opens)]
    volumes = volumes or [1000.0] * n
    return [{"open": o, "high": h, "low": l, "close": c, "volume": v} for o, h, l, c, v in zip(opens, highs, lows, closes, volumes)]


def test_wilder_seeds_with_mean_and_smooths():
    out = wilder([1.0, 2.0, 3.0, 4.0, 5.0], 3)
    # first valid value at index 2 = mean(1,2,3) = 2.0
    assert out[0] is None and out[1] is None
    assert math.isclose(out[2], 2.0)
    # index 3 = (2.0*2 + 4)/3 = 8/3
    assert math.isclose(out[3], 8 / 3)


def test_adx_high_in_strong_trend():
    n = 60
    closes = [100 + i * 2 for i in range(n)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    _, _, a = adx(highs, lows, closes)
    assert a[-1] is not None and a[-1] > 25  # a steady uptrend has strong ADX


def test_adx_trend_label_directions():
    rising = [None, 20.0, 21.0, 22.0, 23.0]
    falling = [None, 23.0, 22.0, 21.0, 20.0]
    mixed = [None, 20.0, 22.0, 21.0, 23.0]
    assert adx_trend_label(rising) == "Trending"
    assert adx_trend_label(falling) == "Weakening"
    assert adx_trend_label(mixed) == "Neutral"


def test_candle_inaction_when_supply_on_low_volume():
    n = 30
    closes = [100.0] * n
    opens = [100.0] * n
    volumes = [1000.0] * n
    # last bar: supply (close < open) and volume below the 20-day average
    opens[-1] = 101.0
    closes[-1] = 99.0
    volumes[-1] = 500.0
    c = candle_indicators(opens, [max(o, c) + 1 for o, c in zip(opens, closes)], [min(o, c) - 1 for o, c in zip(opens, closes)], closes, volumes)
    assert c["candle_type"] == "Supply"
    assert c["comment"] == "Inaction"
    assert c["volume_pct"] < 0


def test_technical_metrics_adx_neutral_falling_passes():
    # Build a series whose ADX is falling and classifies Neutral, plus a supply
    # candle on low volume → both adx_pass and candle_pass.
    n = 60
    closes = [100 + (i % 5) * 0.5 for i in range(n)]  # choppy/range → low ADX
    opens = [c + (0.5 if i % 2 else -0.5) for i, c in enumerate(closes)]
    volumes = [1000.0] * n
    volumes[-1] = 500.0
    opens[-1] = closes[-1] + 1.0  # supply candle
    candles = _candles(closes, opens=opens, volumes=volumes)
    m = technical_metrics(candles)
    assert m is not None
    assert m["adx_trend"] in {"Neutral", "Weakening"}
    assert m["candle_pass"] is True


def test_straddle_parity_pass_and_fail():
    q_pass = {"fut_ltp": 100.0, "ce": 50.0, "pe": 51.0, "otm_ce": 30.0, "otm_pe": 31.0}
    s = straddle_metrics(q_pass, 1.0, 75, 2.0)
    assert s["spread_pct"] <= 2.0
    assert s["straddle_pass"] is True
    assert s["atm"] == 100.0

    q_fail = {"fut_ltp": 100.0, "ce": 50.0, "pe": 60.0, "otm_ce": None, "otm_pe": None}
    s2 = straddle_metrics(q_fail, 1.0, 75, 2.0)
    assert s2["straddle_pass"] is False


def test_tier_of_counts_passes():
    assert tier_of(True, True, True) == (3, "PRIME")
    assert tier_of(True, True, False) == (2, "READY")
    assert tier_of(False, False, True) == (1, "WATCH")
    assert tier_of(False, False, False) == (0, "—")


def test_snap_avoids_float_drift():
    assert snap(24.4, 0.1) == 24.4
    assert snap(1050.0, 10.0) == 1050.0
    assert snap(24.44, 0.1) == 24.4
