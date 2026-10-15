"""Hypothesis property tests for the pure Renko / Point & Figure transforms
using an adaptive percent-of-price box.

Invariants (FEATURE_CHARTS_PLAN.md §6):
  - Renko: brick height is one adaptive box (or `reversal` boxes on a
    reversal); same-direction bricks don't double-count; solid body.
  - P&F: a new column requires a move ≥ `reversal` boxes; columns alternate
    X/O; each column is internally consistent with its own box.
"""

import math
from datetime import datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from app.charts.transforms import build_point_and_figure, build_renko
from app.integrations.definedge_client import Candle

_BASE = datetime(2024, 1, 1, 9, 15)


def _box_at(price: float, box_pct: float) -> float:
    """Mirror of the backend's adaptive box: `box_pct%` of the price magnitude."""
    return (box_pct / 100.0) * abs(price)


def _candles(closes: list[float]) -> list[Candle]:
    out: list[Candle] = []
    for i, c in enumerate(closes):
        out.append(
            Candle(
                ts=(_BASE + timedelta(minutes=i)).isoformat(),
                open=c,
                high=c,
                low=c,
                close=c,
                volume=0,
                oi=None,
            )
        )
    return out


# --- Renko ---


@settings(max_examples=200, deadline=None)
@given(st.lists(st.floats(min_value=1000.0, max_value=2000.0), min_size=2, max_size=150))
def test_renko_bricks_are_valid(closes):
    bricks = build_renko(_candles(closes), 0.5, 3)
    for br in bricks:
        # Solid body: up brick close==high/open==low, down brick the reverse.
        if br["close"] >= br["open"]:
            assert br["close"] == br["high"] and br["open"] == br["low"]
        else:
            assert br["open"] == br["high"] and br["close"] == br["low"]
        # Every brick is at least one adaptive box tall (positive height).
        assert br["high"] - br["low"] > 0


@settings(max_examples=200, deadline=None)
@given(
    st.lists(st.floats(min_value=100.0, max_value=200.0), min_size=2, max_size=150),
    st.integers(min_value=2, max_value=5),
)
def test_renko_brick_height_is_adaptive_box_or_reversal(closes, reversal):
    # Trend bricks are one adaptive box; a reversal brick is exactly
    # `reversal` adaptive boxes. The box is recomputed at each brick's far
    # edge, so an up brick's edge is its low, a down brick's edge its high.
    bricks = build_renko(_candles(closes), 1.0, reversal)
    for br in bricks:
        h = br["high"] - br["low"]
        if br["close"] >= br["open"]:
            edge = br["low"]
        else:
            edge = br["high"]
        box = _box_at(edge, 1.0)
        assert math.isclose(h, box, abs_tol=1e-6) or math.isclose(h, reversal * box, abs_tol=1e-6)


@settings(max_examples=100, deadline=None)
@given(st.lists(st.floats(min_value=100.0, max_value=200.0), min_size=3, max_size=100).map(sorted))
def test_renko_monotonic_up_does_not_double_count(closes):
    bricks = build_renko(_candles(closes), 1.0, 3)
    if not bricks:
        return
    # A monotonic up series produces only up bricks, one adaptive box each,
    # tiling contiguously (no gap, no overlap) — hence no double counting.
    for br in bricks:
        assert br["close"] > br["open"]
        assert math.isclose(br["high"] - br["low"], _box_at(br["low"], 1.0), abs_tol=1e-6)
    for a, b in zip(bricks, bricks[1:], strict=False):
        assert a["high"] == b["low"]


@settings(max_examples=100, deadline=None)
@given(st.lists(st.floats(min_value=100.0, max_value=200.0), min_size=2, max_size=100))
def test_renko_time_is_monotonic(closes):
    bricks = build_renko(_candles(closes), 1.0, 3)
    times = [br["time"] for br in bricks]
    assert times == sorted(times)


# --- Point & Figure ---


@settings(max_examples=200, deadline=None)
@given(st.lists(st.floats(min_value=1000.0, max_value=2000.0), min_size=3, max_size=150))
def test_pnf_columns_align_to_own_box_grid(closes):
    columns = build_point_and_figure(_candles(closes), 0.5, 3)
    for col in columns:
        bs = col["boxSize"]
        assert bs > 0
        # low = bot*box and high = (top+1)*box, so both are integer multiples
        # of the column's *own* box (the grid is per-column, not global).
        assert math.isclose(col["low"] / bs, round(col["low"] / bs), abs_tol=1e-6)
        assert math.isclose(col["high"] / bs, round(col["high"] / bs), abs_tol=1e-6)
        assert col["boxes"] >= 1


@settings(max_examples=200, deadline=None)
@given(st.lists(st.floats(min_value=1000.0, max_value=2000.0), min_size=3, max_size=150))
def test_pnf_column_geometry_is_consistent(closes):
    columns = build_point_and_figure(_candles(closes), 0.5, 3)
    for col in columns:
        assert col["boxSize"] > 0
        assert math.isclose(col["high"] - col["low"], col["boxes"] * col["boxSize"], abs_tol=1e-6)
        # X (close ≥ open) → open==low, close==high; O the reverse.
        if col["close"] >= col["open"]:
            assert col["open"] == col["low"] and col["close"] == col["high"]
        else:
            assert col["open"] == col["high"] and col["close"] == col["low"]


@settings(max_examples=200, deadline=None)
@given(st.lists(st.floats(min_value=1000.0, max_value=2000.0), min_size=3, max_size=150))
def test_pnf_columns_alternate_direction(closes):
    columns = build_point_and_figure(_candles(closes), 0.5, 3)
    dirs = [1 if col["close"] >= col["open"] else -1 for col in columns]
    for a, b in zip(dirs, dirs[1:], strict=False):
        assert a != b  # X/O alternation


@settings(max_examples=100, deadline=None)
@given(st.lists(st.floats(min_value=1000.0, max_value=2000.0), min_size=3, max_size=100))
def test_pnf_time_is_monotonic(closes):
    columns = build_point_and_figure(_candles(closes), 0.5, 3)
    times = [col["time"] for col in columns]
    assert times == sorted(times)
