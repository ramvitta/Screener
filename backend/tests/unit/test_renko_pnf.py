"""Deterministic tests for the pure Renko / Point & Figure transforms using an
adaptive percent-of-price box. Property (hypothesis) invariants live in
tests/property/test_renko_pnf_props.py; this file pins structural behaviour and
edge cases.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.charts.transforms import box_size_from_pct, build_point_and_figure, build_renko, to_utc_seconds
from app.integrations.definedge_client import Candle

_BASE = datetime(2024, 1, 1, 9, 15)
_PCT = 1.0  # 1% of price → a box of ~1.0 around price 100


def _box_at(price: float, pct: float = _PCT) -> float:
    """Mirror of the backend's adaptive box: `pct%` of the price magnitude."""
    return (pct / 100.0) * abs(price)


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
    assert build_renko([], _PCT, 3) == []
    # A single candle only anchors; no brick can form from one bar.
    assert build_renko(_closes([100.0]), _PCT, 3) == []


def test_renko_rejects_bad_box():
    with pytest.raises(ValueError):
        build_renko(_closes([100.0, 101.0]), 0.0, 3)
    with pytest.raises(ValueError):
        build_renko(_closes([100.0, 101.0]), -1.0, 3)


def test_renko_up_bricks_tile_without_double_count():
    bricks = build_renko(_closes([100.0, 105.0]), _PCT, 3)
    assert bricks
    # Every brick is an up brick, one adaptive box tall (box = 1% of its low
    # edge), tiling contiguously (no gap, no overlap → no double counting).
    for br in bricks:
        assert br["close"] > br["open"]
        assert br["high"] - br["low"] == pytest.approx(_box_at(br["low"]))
    for a, b in zip(bricks, bricks[1:], strict=False):
        assert a["high"] == pytest.approx(b["low"])


def test_renko_reversal_brick_spans_reversal_times_box():
    bricks = build_renko(_closes([100.0, 105.0, 100.0]), _PCT, 3)
    assert bricks[0]["close"] > bricks[0]["open"]  # up trend first
    assert bricks[-1]["close"] < bricks[-1]["open"]  # reversal brick last
    # A reversal brick spans `reversal` adaptive boxes at its (high) edge.
    assert bricks[-1]["high"] - bricks[-1]["low"] == pytest.approx(3 * _box_at(bricks[-1]["high"]))


def test_renko_ignores_sub_reversal_retrace():
    # 103 → 101 is a 2-box retrace (below reversal=3): absorbed, no down brick.
    bricks = build_renko(_closes([100.0, 101.0, 102.0, 103.0, 101.0]), _PCT, 3)
    assert bricks and all(br["close"] > br["open"] for br in bricks)


def test_renko_down_trend_and_reversal_up():
    bricks = build_renko(_closes([100.0, 95.0]), _PCT, 3)
    assert bricks and all(br["close"] < br["open"] for br in bricks)


def test_renko_time_is_source_bar():
    bricks = build_renko(_closes([100.0, 102.0]), _PCT, 3)
    # The 100→102 move forms the first brick on bar 1.
    assert bricks[0]["time"] == to_utc_seconds((_BASE + timedelta(minutes=1)).isoformat())


# --- Point & Figure ---


def test_pnf_empty_and_single():
    assert build_point_and_figure([], _PCT, 3) == []
    assert build_point_and_figure(_closes([100.0]), _PCT, 3) == []


def test_pnf_rejects_bad_box():
    with pytest.raises(ValueError):
        build_point_and_figure(_closes([100.0, 101.0]), 0.0, 3)


def test_pnf_single_x_column_exact():
    candles = [
        _candle(0, close=100.0),  # anchor
        _candle(1, close=100.0, high=101.0, low=100.0),  # establish X
        _candle(2, close=101.0, high=102.0, low=101.0),  # extend to top=102
    ]
    columns = build_point_and_figure(candles, _PCT, 3)
    assert len(columns) == 1
    col = columns[0]
    assert col["close"] >= col["open"]  # X
    assert col["open"] == pytest.approx(100.0)
    assert col["close"] == pytest.approx(103.0)  # (top+1)*box = 103
    assert col["low"] == pytest.approx(100.0)
    assert col["high"] == pytest.approx(103.0)
    assert col["boxSize"] == pytest.approx(_box_at(100.0))  # box fixed at establishment price
    assert col["boxes"] == 3


def test_pnf_reversal_produces_two_columns():
    candles = [
        _candle(0, close=100.0),  # anchor top=bot=100
        _candle(1, close=100.0, high=101.0, low=100.0),  # establish X, top=101
        _candle(2, close=101.0, high=102.0, low=101.0),  # extend X, top=102
        _candle(3, close=102.0, high=103.0, low=102.0),  # extend X, top=103
        _candle(4, close=100.0, high=100.0, low=100.0),  # 3-box reversal → O
    ]
    columns = build_point_and_figure(candles, _PCT, 3)
    assert len(columns) == 2

    x_col, o_col = columns
    # X column: boxes 100..103 (4 boxes).
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

    # Each column's geometry is internally consistent with its own box.
    for col in columns:
        assert col["high"] - col["low"] == pytest.approx(col["boxes"] * col["boxSize"])
        assert col["boxSize"] > 0


def test_pnf_column_time_is_establishment_bar():
    candles = [
        _candle(0, close=100.0),
        _candle(1, close=100.0, high=101.0, low=100.0),
    ]
    columns = build_point_and_figure(candles, _PCT, 3)
    assert columns[0]["time"] == to_utc_seconds((_BASE + timedelta(minutes=1)).isoformat())
