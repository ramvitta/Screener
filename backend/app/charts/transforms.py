"""Pure chart transforms for Renko and Point & Figure.

"Python computes, openalgo-charts renders" (FEATURE_CHARTS_PLAN.md §5): these
functions turn 1-min OHLC candles into the brick/column shapes the frontend
feeds directly to openalgo-charts. They are pure (no I/O, no network), so the
exact output is unit- and property-tested.

Output field names follow openalgo-charts' `Bar` contract — `time` (UTC
seconds) + `open/high/low/close` — and P&F columns additionally carry
`boxSize` + `boxes` to match `PointFigureColumn`. Both algorithms mirror
openalgo-charts' own transforms (see `src/transform/renko.ts` and
`src/transform/point-figure.ts`) so the Python output is semantically
identical to what the JS transform would produce for the same input.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from app.integrations.definedge_client import Candle

IST = ZoneInfo("Asia/Kolkata")


def to_utc_seconds(ts: str) -> int:
    """Convert a `Candle.ts` (naive ISO string in Asia/Kolkata wall-clock
    time, see `definedge_client.get_historical_candles`) to the UTC-seconds
    integer openalgo-charts uses for its `time` axis."""
    dt = datetime.fromisoformat(ts)
    return int(dt.replace(tzinfo=IST).timestamp())


def box_size_from_pct(reference_price: float, pct: float) -> float:
    """Box size as a percentage of a series' own reference price
    (FEATURE_CHARTS_PLAN.md §2.10): `box_size = (pct / 100) * reference_price`."""
    return (pct / 100.0) * reference_price


def _adaptive_box(price: float, box_pct: float) -> float:
    """Box size as `box_pct%` of the current price magnitude — openalgo-charts'
    "percent" mode (`box = percent/100 * abs(price)`), recomputed as bricks/
    columns form. Floored above zero so a zero-price bar can't collapse the
    box to a divide-by-zero."""
    box = (box_pct / 100.0) * abs(price)
    return box if box > 0 else 1e-9


def _up_brick(low: float, high: float, time: int) -> dict:
    return {"time": time, "open": low, "high": high, "low": low, "close": high}


def _down_brick(high: float, low: float, time: int) -> dict:
    return {"time": time, "open": high, "high": high, "low": low, "close": low}


def build_renko(candles: List[Candle], box_pct: float, reversal: int) -> List[dict]:
    """Percent-of-price Renko from 1-min OHLC (close-to-close).

    `box_pct` is a percentage (0.1 = 0.1%); the box is recomputed as `box_pct%`
    of the current price magnitude as bricks form (mirroring openalgo-charts'
    "percent" mode), so a series whose price varies widely over the window
    (e.g. an option that went deep ITM and back) does not explode into
    hundreds of thousands of bricks.

    `reversal` = number of boxes the price must retrace (in the opposite
    direction) before a reversal brick forms. A reversal brick spans
    `reversal × box` in a single brick; trend-continuation bricks are one box.

    Returns bricks in openalgo-charts' candle shape (`time` = UTC seconds,
    `open/high/low/close`). A brick's `time` is the source bar that formed
    it, so several bricks may share one bar's time on a large single-bar move.
    """
    if box_pct <= 0:
        raise ValueError("box_pct must be > 0")
    r = max(1, int(reversal))

    bricks: List[dict] = []
    edge: Optional[float] = None  # far edge of the most recent brick
    direction = 0  # +1 up trend, -1 down trend, 0 not yet established

    for candle in candles:
        price = float(candle["close"])
        t = to_utc_seconds(candle["ts"])

        if edge is None:
            # Anchor to the first close's box boundary; no brick yet.
            b = _adaptive_box(price, box_pct)
            edge = math.floor(price / b) * b
            continue

        b = _adaptive_box(edge, box_pct)

        if direction == 0:
            # Establish direction on the first box-sized move.
            if price >= edge + b:
                direction = 1
                while price >= edge + b:
                    bricks.append(_up_brick(edge, edge + b, t))
                    edge += b
                    b = _adaptive_box(edge, box_pct)
            elif price <= edge - b:
                direction = -1
                while price <= edge - b:
                    bricks.append(_down_brick(edge, edge - b, t))
                    edge -= b
                    b = _adaptive_box(edge, box_pct)
            continue

        if direction == 1:
            if price >= edge + b:
                while price >= edge + b:
                    bricks.append(_up_brick(edge, edge + b, t))
                    edge += b
                    b = _adaptive_box(edge, box_pct)
            elif price <= edge - r * b:
                # Retrace of `reversal` boxes → a single reversal brick.
                bricks.append(_down_brick(edge, edge - r * b, t))
                edge -= r * b
                direction = -1
                b = _adaptive_box(edge, box_pct)
                while price <= edge - b:
                    bricks.append(_down_brick(edge, edge - b, t))
                    edge -= b
                    b = _adaptive_box(edge, box_pct)
        else:  # direction == -1
            if price <= edge - b:
                while price <= edge - b:
                    bricks.append(_down_brick(edge, edge - b, t))
                    edge -= b
                    b = _adaptive_box(edge, box_pct)
            elif price >= edge + r * b:
                bricks.append(_up_brick(edge, edge + r * b, t))
                edge += r * b
                direction = 1
                b = _adaptive_box(edge, box_pct)
                while price >= edge + b:
                    bricks.append(_up_brick(edge, edge + b, t))
                    edge += b
                    b = _adaptive_box(edge, box_pct)

    return bricks


def _pnf_column(top: int, bot: int, direction: int, col_time: int, box: float) -> dict:
    """One P&F column as an openalgo-charts `PointFigureColumn` (a `Bar` +
    `boxSize` + `boxes`). `high` is the exclusive top edge of the highest box."""
    low = bot * box
    high = (top + 1) * box
    up = direction >= 0
    return {
        "time": col_time,
        "open": low if up else high,
        "close": high if up else low,
        "high": high,
        "low": low,
        "boxSize": box,
        "boxes": top - bot + 1,
    }


def build_point_and_figure(candles: List[Candle], box_pct: float, reversal: int) -> List[dict]:
    """Classic point & figure (method='hl') with percent-of-price box.

    A new column starts only after a reversal of `reversal` boxes. The box is
    fixed *within* a column — set to `box_pct%` of the price that established
    the column — and recomputed when a new column starts (mirroring
    openalgo-charts' "percent" mode). Each emitted column matches
    openalgo-charts' `PointFigureColumn` (a `Bar` + `boxSize` + `boxes`):
    `high` is the exclusive top edge of the highest box, `low` the bottom
    edge of the lowest box, and open/close encode the X (close ≥ open) /
    O (close < open) direction.
    """
    if box_pct <= 0:
        raise ValueError("box_pct must be > 0")
    r = max(1, int(reversal))

    # Column state as integer box indices, mirroring openalgo-charts'
    # PointFigureTransform (`_dir`/`_top`/`_bot`/`_time`).
    direction = 0  # +1 X, -1 O, 0 not yet established
    top = 0
    bot = 0
    box = 0.0  # box fixed within a column, recomputed at each reversal
    anchored = False
    col_time = 0  # UTC seconds of the bar that established this column

    columns: List[dict] = []

    for candle in candles:
        up_price = float(candle["high"])
        down_price = float(candle["low"])
        t = to_utc_seconds(candle["ts"])

        if not anchored:
            # First bar: anchor only — no column, no direction.
            box = _adaptive_box(float(candle["close"]), box_pct)
            box_index = math.floor(float(candle["close"]) / box)
            top = box_index
            bot = box_index
            direction = 0
            col_time = t
            anchored = True
            continue

        hb = math.floor(up_price / box)
        lb = math.floor(down_price / box)

        if direction == 0:
            up_move = hb - top
            down_move = bot - lb
            if up_move <= 0 and down_move <= 0:
                continue
            if up_move >= down_move:
                top = hb
                direction = 1
            else:
                bot = lb
                direction = -1
            col_time = t
            continue

        if direction > 0:
            if hb > top:
                top = hb  # extend the X column
            elif top - lb >= r:
                # Reversal: emit the X column, then start an O column at the
                # box just below the X column's highest box (box recomputed
                # at the new, lower price).
                boundary = top * box
                columns.append(_pnf_column(top, bot, direction, col_time, box))
                box = _adaptive_box(down_price, box_pct)
                top = math.ceil(boundary / box) - 1
                bot = math.floor(down_price / box)
                direction = -1
                col_time = t
        else:
            if lb < bot:
                bot = lb  # extend the O column
            elif hb - bot >= r:
                boundary = (bot + 1) * box
                columns.append(_pnf_column(top, bot, direction, col_time, box))
                box = _adaptive_box(up_price, box_pct)
                bot = math.floor(boundary / box)
                top = math.floor(up_price / box)
                direction = 1
                col_time = t

    # Flush the in-progress column (nothing to emit until a direction exists).
    if direction != 0 and anchored:
        columns.append(_pnf_column(top, bot, direction, col_time, box))

    return columns
