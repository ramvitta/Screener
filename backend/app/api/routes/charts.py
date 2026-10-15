from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.api.routes.auth import _client, ensure_authenticated
from app.charts.transforms import box_size_from_pct, build_point_and_figure, build_renko
from app.integrations import chart_history
from app.integrations.definedge_client import Candle, DefinedgeAuthError, DefinedgeClient
from app.integrations.instruments import (
    ContractNotFound,
    ResolvedContract,
    find_future,
    find_option,
    get_master_rows,
    resolve_nifty_atm_contracts,
)

router = APIRouter(prefix="/api/v1/charts", tags=["charts"])

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)
VALID_INTERVALS = {1, 3, 5, 10, 15, 30, 60}

# Safety cap for a single series' Renko/P&F output. Adaptive percent-of-price
# boxes already avoid the fixed-box blowup; this is the belt-and-suspenders
# net that auto-widens the box until the count fits, then hard-truncates.
MAX_BRICKS = 3000
MAX_BOX_BUMPS = 8  # doubling box_pct up to 8× → 256× the requested box


def _resample(candles: List[Candle], interval_minutes: int) -> List[Candle]:
    """Aggregates 1-min candles into `interval_minutes` buckets — Definedge's
    historical API only serves raw 1-min/day/tick, so multi-minute timeframes
    are built server-side from the 1-min series."""
    if interval_minutes <= 1 or not candles:
        return candles

    buckets: dict = {}
    order: List[datetime] = []
    for c in candles:
        ts = datetime.fromisoformat(c["ts"])
        bucket_minute = (ts.hour * 60 + ts.minute) // interval_minutes * interval_minutes
        bucket_ts = ts.replace(hour=bucket_minute // 60, minute=bucket_minute % 60, second=0, microsecond=0)
        if bucket_ts not in buckets:
            buckets[bucket_ts] = []
            order.append(bucket_ts)
        buckets[bucket_ts].append(c)

    resampled: List[Candle] = []
    for bucket_ts in order:
        group = buckets[bucket_ts]
        oi_values = [c["oi"] for c in group if c["oi"] is not None]
        resampled.append(
            Candle(
                ts=bucket_ts.isoformat(),
                open=group[0]["open"],
                high=max(c["high"] for c in group),
                low=min(c["low"] for c in group),
                close=group[-1]["close"],
                volume=sum(c["volume"] for c in group),
                oi=oi_values[-1] if oi_values else None,
            )
        )
    return resampled


def _today_session_window() -> Tuple[datetime, datetime]:
    now_ist = datetime.now(IST)
    open_dt = now_ist.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
    close_dt = now_ist.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)
    end = min(now_ist, close_dt)
    return open_dt.replace(tzinfo=None), end.replace(tzinfo=None)


async def _previous_close(client: DefinedgeClient, contract: ResolvedContract) -> Optional[float]:
    """Last completed trading day's close, via a daily-candle lookback —
    Definedge's quote payload doesn't include the previous close directly."""
    end = datetime.now(IST).replace(tzinfo=None)
    start = end - timedelta(days=10)
    candles = await client.get_historical_candles(contract.exchange, contract.token, "day", start, end)
    today = date.today()
    prior = [c for c in candles if datetime.fromisoformat(c["ts"]).date() < today]
    return prior[-1]["close"] if prior else None


async def _series_for(client: DefinedgeClient, contract: ResolvedContract, label: str, interval_minutes: int) -> dict:
    quote = await client.get_quote(contract.exchange, contract.token)
    start, end = _today_session_window()
    candles: List[Candle] = []
    if start < end:
        candles = await client.get_historical_candles(contract.exchange, contract.token, "minute", start, end)
        candles = _resample(candles, interval_minutes)
    prev_close = await _previous_close(client, contract)
    ltp = float(quote["ltp"])
    change = ltp - prev_close if prev_close else None
    change_pct = (change / prev_close * 100) if change is not None and prev_close else None

    return {
        "label": label,
        "trading_symbol": contract.trading_symbol,
        "strike": contract.strike,
        "expiry": contract.expiry.isoformat() if contract.expiry else None,
        "ltp": ltp,
        "day_open": float(quote.get("day_open", 0)) or None,
        "day_high": float(quote.get("day_high", 0)) or None,
        "day_low": float(quote.get("day_low", 0)) or None,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "candles": candles,
    }


def _combine_candles(leg_candles: List[List[Candle]], actions: List[str]) -> List[Candle]:
    """Net position value across legs as 1-min candles: BUY contributes
    +price, SELL -price — i.e. what it would cost to close the spread. A
    bar's high/low can't be read off simultaneously-observed leg extremes,
    so each is the achievable bound: high = sum of each leg's most favorable
    extreme toward a higher combined value, low = the least favorable bound.
    Open/close are exact (each leg's real open/close, summed)."""
    signs = [1 if a == "BUY" else -1 for a in actions]
    ts_sets = [{c["ts"] for c in candles} for candles in leg_candles]
    common_ts = sorted(set.intersection(*ts_sets)) if ts_sets and all(ts_sets) else []
    candle_maps = [{c["ts"]: c for c in candles} for candles in leg_candles]

    combined: List[Candle] = []
    for ts in common_ts:
        rows = [cm[ts] for cm in candle_maps]
        combined.append(
            Candle(
                ts=ts,
                open=sum(sign * r["open"] for sign, r in zip(signs, rows)),
                high=sum((r["high"] if sign > 0 else -r["low"]) for sign, r in zip(signs, rows)),
                low=sum((r["low"] if sign > 0 else -r["high"]) for sign, r in zip(signs, rows)),
                close=sum(sign * r["close"] for sign, r in zip(signs, rows)),
                volume=sum(r["volume"] for r in rows),
                oi=None,
            )
        )
    return combined


def _combine_series(leg_series: List[dict], actions: List[str], label: str) -> dict:
    """Combined-series dict for the /signal endpoint (label + ltp/change on
    top of the combined 1-min candles from `_combine_candles`)."""
    signs = [1 if a == "BUY" else -1 for a in actions]
    combined_candles = _combine_candles([s["candles"] for s in leg_series], actions)

    ltp = sum(sign * s["ltp"] for sign, s in zip(signs, leg_series))
    have_prev_close = all(s["prev_close"] is not None for s in leg_series)
    prev_close = sum(sign * s["prev_close"] for sign, s in zip(signs, leg_series)) if have_prev_close else None
    change = ltp - prev_close if prev_close is not None else None
    change_pct = (change / abs(prev_close) * 100) if change is not None and prev_close else None

    return {
        "label": label,
        "trading_symbol": label,
        "strike": None,
        "expiry": leg_series[0]["expiry"] if leg_series else None,
        "ltp": ltp,
        "day_open": None,
        "day_high": None,
        "day_low": None,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "candles": combined_candles,
    }


class SignalLeg(BaseModel):
    strike: float
    option_type: str
    action: str


class SignalChartsRequest(BaseModel):
    symbol: str
    expiry: date
    legs: List[SignalLeg]


@router.post("/signal")
async def signal_charts(payload: SignalChartsRequest, interval: int = Query(1, description="Candle size in minutes")):
    """Chart grid for a specific screener signal's actual legs — e.g. a Bull
    Put Spread SELL 21800 PE / BUY 21700 PE gives: the underlying future,
    each leg's own chart, and the combined net spread value. Not the generic
    ATM contracts from GET /nifty."""
    if interval not in VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"interval must be one of {sorted(VALID_INTERVALS)}")
    if not payload.legs:
        raise HTTPException(status_code=400, detail="At least one leg is required")

    try:
        await ensure_authenticated()
        rows = await get_master_rows(_client)
        future = find_future(rows, payload.symbol, date.today())
        leg_contracts = [
            find_option(rows, payload.symbol, payload.expiry, leg.strike, leg.option_type) for leg in payload.legs
        ]
    except DefinedgeAuthError:
        raise HTTPException(status_code=401, detail="Not authenticated with Definedge") from None
    except ContractNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None

    future_series = await _series_for(_client, future, f"{payload.symbol}-I", interval)
    leg_series = [
        await _series_for(
            _client, contract, f"{leg.action} {payload.symbol} {int(leg.strike)} {leg.option_type}", interval
        )
        for contract, leg in zip(leg_contracts, payload.legs)
    ]

    combined = None
    if len(leg_series) >= 2:
        label = " + ".join(f"{leg.action} {int(leg.strike)}{leg.option_type}" for leg in payload.legs)
        combined = _combine_series(leg_series, [leg.action for leg in payload.legs], label)

    return {
        "as_of": datetime.now(IST).isoformat(),
        "interval_minutes": interval,
        "series": {
            "future": future_series,
            "legs": leg_series,
            "combined": combined,
        },
    }


@router.get("/nifty")
async def nifty_atm_charts(interval: int = Query(1, description="Candle size in minutes")):
    """Real, live chart data for the Contract Detail 2x2 grid: NIFTY
    near-month future, NIFTY 50 spot, and the ATM CE/PE for the nearest
    weekly expiry (see instruments.resolve_nifty_atm_contracts). ATM is
    always the strike nearest spot, rounded to NIFTY's 50-point interval.
    All four series share one `interval`, so switching timeframe keeps them
    synchronized."""
    if interval not in VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"interval must be one of {sorted(VALID_INTERVALS)}")

    try:
        await ensure_authenticated()
        resolved = await resolve_nifty_atm_contracts(_client)
    except DefinedgeAuthError:
        raise HTTPException(status_code=401, detail="Not authenticated with Definedge") from None
    except StopIteration:
        raise HTTPException(status_code=502, detail="Could not resolve NIFTY contracts from instrument master") from None

    future_series = await _series_for(_client, resolved.future, "NIFTY-I", interval)
    spot_series = await _series_for(_client, resolved.spot, "Nifty 50", interval)
    ce_series = await _series_for(
        _client, resolved.ce, f"NIFTY {resolved.options_expiry.isoformat()} CE {int(resolved.atm_strike)}", interval
    )
    pe_series = await _series_for(
        _client, resolved.pe, f"NIFTY {resolved.options_expiry.isoformat()} PE {int(resolved.atm_strike)}", interval
    )

    return {
        "as_of": datetime.now(IST).isoformat(),
        "atm_strike": resolved.atm_strike,
        "options_expiry": resolved.options_expiry.isoformat(),
        "interval_minutes": interval,
        "series": {
            "future": future_series,
            "spot": spot_series,
            "ce": ce_series,
            "pe": pe_series,
        },
    }


class TransformsRequest(BaseModel):
    symbol: str
    expiry: date
    legs: List[SignalLeg]
    box_pct: float = 0.1
    combined_box_pct: float = 0.5
    reversal: int = 3


def _transform_candles(candles: List[Candle], box_pct: float, reversal: int, interval: int) -> dict:
    """Reference price, box info, and the three views (resampled candlesticks
    + Renko + P&F) for one series. Renko/P&F always use the 1-min base
    (independent of the candlestick interval) per FEATURE_CHARTS_PLAN.md §8.

    Renko/P&F use an adaptive percent-of-price box (`box_pct%` of the current
    price level, recomputed as bricks/columns form). If a series still yields
    more than `MAX_BRICKS`, the box percentage is doubled until it fits (the
    effective `box_pct` is reported back), with a final hard truncation as a
    last resort."""
    if not candles:
        return {
            "reference_price": None, "box_size": None, "box_pct": None,
            "candles": [], "renko": [], "pnf": [],
        }
    reference = abs(candles[-1]["close"])

    effective = box_pct
    renko: List[dict] = []
    pnf: List[dict] = []
    for _ in range(MAX_BOX_BUMPS + 1):
        renko = build_renko(candles, effective, reversal)
        pnf = build_point_and_figure(candles, effective, reversal)
        if len(renko) <= MAX_BRICKS and len(pnf) <= MAX_BRICKS:
            break
        effective *= 2.0

    return {
        "reference_price": reference,
        "box_size": box_size_from_pct(reference, effective),
        "box_pct": effective,
        "candles": _resample(candles, interval),
        "renko": renko[:MAX_BRICKS],
        "pnf": pnf[:MAX_BRICKS],
    }


def _build_series(
    label: str,
    trading_symbol: str,
    strike,
    expiry,
    candles: List[Candle],
    box_pct: float,
    reversal: int,
    interval: int,
) -> dict:
    t = _transform_candles(candles, box_pct, reversal, interval)
    return {
        "label": label,
        "trading_symbol": trading_symbol,
        "strike": strike,
        "expiry": expiry.isoformat() if expiry else None,
        "reference_price": t["reference_price"],
        "box_size": t["box_size"],
        "box_pct": t["box_pct"],
        "candles": t["candles"],
        "renko": t["renko"],
        "pnf": t["pnf"],
    }


@router.post("/transforms")
async def chart_transforms(
    payload: TransformsRequest,
    interval: int = Query(5, description="Candlestick size in minutes (Renko/P&F stay 1-min)"),
):
    """P&F / Renko / candlestick transforms for a signal's actual contracts.

    For each series (future, legs, combined) returns raw candles resampled to
    `interval`, plus `renko` and `pnf` built from the fixed 1-min base (§8).
    Box size defaults to `box_pct`% of each series' own reference price
    (combined uses `combined_box_pct`); `reversal` is the P&F/Renko reversal.
    """
    if interval not in VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"interval must be one of {sorted(VALID_INTERVALS)}")
    if not payload.legs:
        raise HTTPException(status_code=400, detail="At least one leg is required")
    if payload.box_pct <= 0 or payload.combined_box_pct <= 0:
        raise HTTPException(status_code=400, detail="box_pct and combined_box_pct must be positive")
    if payload.reversal < 1:
        raise HTTPException(status_code=400, detail="reversal must be >= 1")

    try:
        await ensure_authenticated()
        rows = await get_master_rows(_client)
        future = find_future(rows, payload.symbol, date.today())
        leg_contracts = [
            find_option(rows, payload.symbol, payload.expiry, leg.strike, leg.option_type) for leg in payload.legs
        ]
    except DefinedgeAuthError:
        raise HTTPException(status_code=401, detail="Not authenticated with Definedge") from None
    except ContractNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None

    future_candles = await chart_history.ensure_chart_history(
        _client, exchange=future.exchange, token=future.token, symbol=payload.symbol,
        kind="future", expiry=future.expiry,
    )
    leg_candles = [
        await chart_history.ensure_chart_history(
            _client, exchange=c.exchange, token=c.token, symbol=payload.symbol,
            kind="option", expiry=c.expiry, strike=leg.strike, option_type=leg.option_type,
        )
        for c, leg in zip(leg_contracts, payload.legs)
    ]

    future_series = _build_series(
        f"{payload.symbol}-I", future.trading_symbol, future.strike, future.expiry,
        future_candles, payload.box_pct, payload.reversal, interval,
    )
    leg_series = [
        _build_series(
            f"{leg.action} {payload.symbol} {int(leg.strike)} {leg.option_type}",
            c.trading_symbol, c.strike, c.expiry,
            candles, payload.box_pct, payload.reversal, interval,
        )
        for c, leg, candles in zip(leg_contracts, payload.legs, leg_candles)
    ]

    combined = None
    if len(leg_series) >= 2:
        label = " + ".join(f"{leg.action} {int(leg.strike)}{leg.option_type}" for leg in payload.legs)
        combined_candles = _combine_candles(leg_candles, [leg.action for leg in payload.legs])
        combined = _build_series(
            label, label, None, leg_contracts[0].expiry,
            combined_candles, payload.combined_box_pct, payload.reversal, interval,
        )

    return {
        "as_of": datetime.now(IST).isoformat(),
        "interval_minutes": interval,
        "reversal": payload.reversal,
        "series": {
            "future": future_series,
            "legs": leg_series,
            "combined": combined,
        },
    }
