from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.api.routes.auth import _client, ensure_authenticated
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


def _combine_series(leg_series: List[dict], actions: List[str], label: str) -> dict:
    """Net position value across legs: BUY contributes +price, SELL -price —
    i.e. what it would cost to close the whole spread right now. A bar's
    high/low can't be read off simultaneously-observed leg extremes, so each
    is the achievable bound: high = sum of each leg's most favorable extreme
    toward a higher combined value, low = the least favorable bound. Open/
    close are exact (each leg's real open/close, summed)."""
    signs = [1 if a == "BUY" else -1 for a in actions]

    ts_sets = [{c["ts"] for c in s["candles"]} for s in leg_series]
    common_ts = sorted(set.intersection(*ts_sets)) if ts_sets and all(ts_sets) else []

    candle_maps = [{c["ts"]: c for c in s["candles"]} for s in leg_series]
    combined_candles: List[Candle] = []
    for ts in common_ts:
        rows = [cm[ts] for cm in candle_maps]
        combined_candles.append(
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
