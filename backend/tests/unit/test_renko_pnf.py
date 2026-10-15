"""Deterministic tests for the pure Renko / Point & Figure transforms.

Property (hypothesis) invariants live in tests/property/test_renko_pnf_props.py;
this file pins exact outputs and edge cases.
"""

from datetime import datetime, timedelta

import pytest

from app.charts.transforms import box_size_from_pct, build_point_and_figure, build_renko, to_utc_seconds
from app.integrations.definedge_client import Candle

_BASE = datetime(2024, 1, 1, 9, 15)


def _candle(i: int, close: float, high: float | None = None, low: float | None = None, open_: float | None = None) -> Candle:
    ts = (_BASE + timedelta(minutes=i)).isoformat()
    return Candle(
        ts=ts,
        open=open_ if open_ is not None else close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=0,
        oi=None,
    )


def _closes(values: list[float]) -> list[Candle]:
    return [_candle(i, c) for i, c in enumerate(values)]


# --- time conversion ---


def test_to_utc_seconds_ist_offset():
    # 09:15 IST == 03:45 UTC == epoch 1704080700 (Asia/Kolkata has no DST).
    assert to_utc_seconds("2024-01-01T09:15:00") == 1704080700


# --- box size derivation ---


def test_box_size_from_pct():
    assert box_size_from_pct(1000.0, 0.1) == pytest.approx(1.0)
    assert box_size_from_pct(23000.0, 0.5) == pytest.approx(115.0)
    assert box_size_from_pct(23000.0, 3.0) == pytest.approx(690.0)


# --- Renko ---


def test_renko_empty_and_single():
    assert build_renko([], 1.0, 3) == []
    # A single candle only anchors; no brick can form from one bar.
    assert build_renko(_closes([100.0]), 1.0, 3) == []


def test_renko_rejects_bad_box():
    with pytest.raises(ValueError):
        build_renko(_closes([100.0, 101.0]), 0.0, 3)
    with pytest.raises(ValueError):
        build_renko(_closes([100.0, 101.0]), -1.0, 3)


def test_renko_up_bricks_tile_without_double_count():
    # A single candle closing 5 boxes above the anchor forms exactly 5 up
    # bricks (no double counting), each one box tall, tiling contiguously.
    bricks = build_renko(_closes([100.0, 105.0]), 1.0, 3)
    assert len(bricks) == 5
    for br in bricks:
        assert br["close"] > br["open"]
        assert br["high"] - br["low"] == pytest.approx(1.0)
    for a, b in zip(bricks, bricks[1:]):
        assert a["high"] == b["low"]


def test_renko_reversal_requires_reversal_times_box():
    # Up 100→103 (three 1-box bricks), then retrace. A retrace of 2 boxes is
    # not enough (reversal=3); a full 3-box retrace forms one reversal brick.
    bricks = build_renko(_closes([100.0, 101.0, 102.0, 103.0, 101.0]), 1.0, 3)
    assert len(bricks) == 3  # 100→103, the 2-box retrace to 101 is ignored
    assert all(br["close"] > br["open"] for br in bricks)

    bricks = build_renko(_closes([100.0, 101.0, 102.0, 103.0, 100.0]), 1.0, 3)
    assert len(bricks) == 4  # three up bricks + one reversal brick
    reversal = bricks[-1]
    assert reversal["close"] < reversal["open"]
    assert reversal["high"] - reversal["low"] == pytest.approx(3.0)  # reversal × box
    assert reversal["open"] == pytest.approx(103.0)
    assert reversal["close"] == pytest.approx(100.0)


def test_renko_down_trend_and_reversal_up():
    bricks = build_renko(_closes([100.0, 99.0, 98.0]), 1.0, 3)
    assert len(bricks) == 2
    assert all(br["close"] < br["open"] for br in bricks)


def test_renko_time_is_source_bar():
    bricks = build_renko(_closes([100.0, 101.0, 102.0]), 1.0, 3)
    # Brick for the 100→101 move carries bar index 1's time; 101→102 carries bar 2's.
    assert bricks[0]["time"] == to_utc_seconds((_BASE + timedelta(minutes=1)).isoformat())
    assert bricks[1]["time"] == to_utc_seconds((_BASE + timedelta(minutes=2)).isoformat())


# --- Point & Figure ---


def test_pnf_empty_and_single():
    assert build_point_and_figure([], 1.0, 3) == []
    assert build_point_and_figure(_closes([100.0]), 1.0, 3) == []


def test_pnf_rejects_bad_box():
    with pytest.raises(ValueError):
        build_point_and_figure(_closes([100.0, 101.0]), 0.0, 3)


def test_pnf_single_x_column_exact():
    candles = [
        _candle(0, close=100.0),  # anchor
        _candle(1, close=100.0, high=101.0, low=100.0),  # establish X
        _candle(2, close=101.0, high=102.0, low=101.0),  # extend to top=102
    ]
    columns = build_point_and_figure(candles, 1.0, 3)
    assert len(columns) == 1
    col = columns[0]
    assert col["close"] >= col["open"]  # X
    assert col["open"] == pytest.approx(100.0)
    assert col["close"] == pytest.approx(103.0)  # (top+1)*box = 103
    assert col["low"] == pytest.approx(100.0)
    assert col["high"] == pytest.approx(103.0)
    assert col["boxSize"] == pytest.approx(1.0)
    assert col["boxes"] == 3


def test_pnf_reversal_exact_columns():
    candles = [
        _candle(0, close=100.0),  # anchor top=bot=100
        _candle(1, close=100.0, high=101.0, low=100.0),  # establish X, top=101
        _candle(2, close=101.0, high=102.0, low=101.0),  # extend X, top=102
        _candle(3, close=102.0, high=103.0, low=102.0),  # extend X, top=103
        _candle(4, close=100.0, high=100.0, low=100.0),  # 3-box reversal → O
    ]
    columns = build_point_and_figure(candles, 1.0, 3)
    assert len(columns) == 2

    x_col, o_col = columns
    # X column: boxes 100..103 (4 boxes), emitted at the reversal bar.
    assert x_col["close"] >= x_col["open"]
    assert x_col["low"] == pytest.approx(100.0)
    assert x_col["high"] == pytest.approx(104.0)
    assert x_col["boxes"] == 4

    # O column: top = ceil(103/1)-1 = 102, bot = floor(100/1) = 100 → 3 boxes.
    assert o_col["close"] < o_col["open"]
    assert o_col["low"] == pytest.approx(100.0)
    assert o_col["high"] == pytest.approx(103.0)
    assert o_col["boxes"] == 3
    assert o_col["open"] == pytest.approx(103.0)
    assert o_col["close"] == pytest.approx(100.0)


def test_pnf_column_time_is_establishment_bar():
    candles = [
        _candle(0, close=100.0),
        _candle(1, close=100.0, high=101.0, low=100.0),
    ]
    columns = build_point_and_figure(candles, 1.0, 3)
    assert columns[0]["time"] == to_utc_seconds((_BASE + timedelta(minutes=1)).isoformat())
