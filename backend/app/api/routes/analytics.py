"""Phase 3 of the UI gap-closing plan: payoff graph + target/time slider.
Computes P&L curves over a spot-price grid for an arbitrary leg combo (a
live screener signal, an open position, or a what-if combo the user hasn't
logged yet) — read-only analytics, no order placement."""

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.api.routes.auth import _client, ensure_authenticated
from app.db.sqlite import get_conn
from app.integrations import instruments
from app.integrations.definedge_client import DefinedgeAuthError
from app.models.schemas import (
    KellySizingResponse,
    OiPcrResponse,
    OiRow,
    PayoffRequest,
    PayoffResponse,
    SessionIvPoint,
    Symbol,
)
from app.screener import pricing
from app.screener.pricing import time_to_expiry_years

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

GRID_POINTS = 61
GRID_BAND_PCT = 0.12  # ±12% of spot, enough to show both wings' max profit/loss plateaus
SESSION_IV_MAX_POINTS = 80  # downsample the session's 1-min bars so the payload stays small
OVER_ALLOCATED_PCT = 150.0  # heuristic threshold for the Kelly panel's warning badge


@dataclass
class _ResolvedLeg:
    option_type: str
    action: str
    qty: int
    strike: float
    entry_price: float
    iv: float


def _resolve_leg(leg, spot: float, t_years_expiry: float) -> _ResolvedLeg:
    """A screener-signal leg already carries a real iv — a logged position's
    doesn't, so it sends iv=0 plus its live ltp as current_price and we solve
    iv from that instead (today's market read, not a stale entry-day value).

    entry_price similarly falls back to today's live theoretical premium
    (Black-Scholes at the current spot) when omitted — the "what if I
    entered this combo right now" case for a not-yet-logged signal."""
    if leg.iv > 0:
        iv = leg.iv
    else:
        price = leg.current_price if leg.current_price is not None else (leg.entry_price or spot * 0.01)
        solved = pricing.implied_volatility(price, spot, leg.strike, t_years_expiry, leg.option_type.value)
        iv = solved if solved > 0 else 0.15  # flat fallback if the solve fails (e.g. price <= intrinsic)

    entry_price = leg.entry_price
    if entry_price is None:
        entry_price = pricing.bs_price(spot, leg.strike, t_years_expiry, iv, leg.option_type.value)

    return _ResolvedLeg(
        option_type=leg.option_type.value, action=leg.action.value, qty=leg.qty, strike=leg.strike, entry_price=entry_price, iv=iv
    )


def _leg_pnl(leg: _ResolvedLeg, spot: float, t_years: float) -> float:
    value = pricing.bs_price(spot, leg.strike, max(t_years, 0.0), leg.iv, leg.option_type)
    diff = leg.entry_price - value if leg.action == "SELL" else value - leg.entry_price
    return diff * leg.qty


def _breakevens(spot_range: List[float], at_expiry: List[float]) -> List[float]:
    """Linear-interpolated zero-crossings of the expiry P&L curve."""
    points = []
    for i in range(1, len(at_expiry)):
        y0, y1 = at_expiry[i - 1], at_expiry[i]
        if y0 == 0:
            points.append(spot_range[i - 1])
        elif (y0 < 0) != (y1 < 0):
            x0, x1 = spot_range[i - 1], spot_range[i]
            frac = -y0 / (y1 - y0)
            points.append(round(x0 + frac * (x1 - x0), 2))
    return points


@router.post("/payoff", response_model=PayoffResponse)
async def payoff(payload: PayoffRequest):
    if not payload.legs:
        raise HTTPException(status_code=400, detail="At least one leg is required")

    try:
        await ensure_authenticated()
        rows = await instruments.get_master_rows(_client)
        spot_contract = instruments.find_spot(rows, payload.symbol)
        quote = await _client.get_quote(spot_contract.exchange, spot_contract.token)
        spot = float(quote["ltp"])
    except DefinedgeAuthError:
        raise HTTPException(status_code=503, detail="Broker session unavailable") from None
    if not math.isfinite(spot) or spot <= 0:
        raise HTTPException(status_code=502, detail="Broker returned an implausible spot price")

    today = date.today()
    target_date = payload.target_date or today
    t_years_expiry = max((payload.expiry - today).days, 0) / 365
    t_years_target = max((payload.expiry - target_date).days, 0) / 365

    resolved_legs = [_resolve_leg(leg, spot, t_years_expiry) for leg in payload.legs]

    lo = spot * (1 - GRID_BAND_PCT)
    hi = spot * (1 + GRID_BAND_PCT)
    step = (hi - lo) / (GRID_POINTS - 1)
    spot_range = [round(lo + i * step, 2) for i in range(GRID_POINTS)]

    at_expiry = [round(sum(_leg_pnl(leg, s, 0.0) for leg in resolved_legs), 2) for s in spot_range]
    at_target = [round(sum(_leg_pnl(leg, s, t_years_target) for leg in resolved_legs), 2) for s in spot_range]

    return PayoffResponse(
        spot_range=spot_range,
        current_spot=spot,
        at_expiry=at_expiry,
        at_target=at_target,
        breakevens=_breakevens(spot_range, at_expiry),
        max_profit=max(at_expiry),
        max_loss=min(at_expiry),
        target_date=target_date,
    )


async def _latest_session_candles(exchange: str, token: str):
    """Definedge's live quote payload carries no OI field at all — confirmed
    against a real quote response — but the 1-min historical-candle endpoint
    does. This pulls the most recent completed session's candles (today's if
    the market is open, otherwise the last trading day's) purely to read
    real OI/volume/close off them; never fabricated."""
    end = datetime.now()
    start = end - timedelta(days=4)  # covers a long weekend without a trading session
    candles = await _client.get_historical_candles(exchange, token, "minute", start, end)
    if not candles:
        return []
    latest_date = datetime.fromisoformat(candles[-1]["ts"]).date()
    return [c for c in candles if datetime.fromisoformat(c["ts"]).date() == latest_date]


@router.get("/oi-pcr", response_model=OiPcrResponse)
async def oi_pcr(symbol: Symbol = "NIFTY", depth: int = Query(5, ge=1, le=10)):
    """OI change / PCR panel (Phase 5): real open interest and volume pulled
    from the historical-candle endpoint (see _latest_session_candles), plus
    a real intraday ATM-IV series computed from that same session's candles
    — no synthetic history, no placeholder numbers."""
    try:
        await ensure_authenticated()
        rows = await instruments.get_master_rows(_client)
        spot_contract = instruments.find_spot(rows, symbol)
        spot_candles = await _latest_session_candles(spot_contract.exchange, spot_contract.token)
        if not spot_candles:
            raise HTTPException(status_code=503, detail="No recent session data available from the broker")
        spot = spot_candles[-1]["close"]
        atm_strike = round(spot / instruments.NIFTY_STRIKE_INTERVAL) * instruments.NIFTY_STRIKE_INTERVAL
        expiry = instruments.nearest_expiry(rows, symbol, date.today())
    except DefinedgeAuthError:
        raise HTTPException(status_code=503, detail="Broker session unavailable") from None
    if not math.isfinite(spot) or spot <= 0:
        raise HTTPException(status_code=502, detail="Broker returned an implausible spot price")

    oi_rows: List[OiRow] = []
    call_oi_total = put_oi_total = call_vol_total = put_vol_total = 0
    atm_ce_candles: List[dict] = []
    latest_ts: Optional[datetime] = None

    for offset in range(-depth, depth + 1):
        strike = atm_strike + offset * instruments.NIFTY_STRIKE_INTERVAL
        row = OiRow(otm=offset, strike=strike)
        for option_type in ("CE", "PE"):
            try:
                contract = instruments.find_option(rows, symbol, expiry, strike, option_type)
                candles = await _latest_session_candles(contract.exchange, contract.token)
                if not candles:
                    continue
                oi = int(candles[-1]["oi"] or 0)
                oi_change = oi - int(candles[0]["oi"] or 0)
                vol = int(candles[-1]["volume"] or 0)
                ts = datetime.fromisoformat(candles[-1]["ts"])
                latest_ts = ts if latest_ts is None else max(latest_ts, ts)
            except Exception as exc:  # noqa: BLE001 — contract may not exist at this strike/expiry
                logger.debug("oi_pcr: no candles for %s %s %s (%s)", symbol, strike, option_type, type(exc).__name__)
                continue
            if option_type == "CE":
                row.call_oi, row.call_oi_change = oi, oi_change
                call_oi_total += oi
                call_vol_total += vol
                if offset == 0:
                    atm_ce_candles = candles
            else:
                row.put_oi, row.put_oi_change = oi, oi_change
                put_oi_total += oi
                put_vol_total += vol
        oi_rows.append(row)

    session_iv: List[SessionIvPoint] = []
    if atm_ce_candles:
        step = max(1, len(atm_ce_candles) // SESSION_IV_MAX_POINTS)
        for c in atm_ce_candles[::step]:
            ts = datetime.fromisoformat(c["ts"])
            t_years = time_to_expiry_years(expiry, ts.replace(tzinfo=None) if ts.tzinfo else ts)
            iv = pricing.implied_volatility(c["close"], spot, atm_strike, t_years, "CE")
            if iv > 0:
                session_iv.append(SessionIvPoint(ts=ts, atm_iv=round(iv, 4)))

    return OiPcrResponse(
        symbol=symbol,
        expiry=expiry,
        spot=spot,
        atm_strike=atm_strike,
        as_of=latest_ts,
        call_oi_total=call_oi_total,
        put_oi_total=put_oi_total,
        oi_pcr=round(put_oi_total / call_oi_total, 3) if call_oi_total else None,
        call_vol_total=call_vol_total,
        put_vol_total=put_vol_total,
        vol_pcr=round(put_vol_total / call_vol_total, 3) if call_vol_total else None,
        rows=oi_rows,
        session_iv=session_iv,
    )


@router.get("/kelly", response_model=KellySizingResponse)
def kelly_sizing(capital: float = Query(..., gt=0, le=1_000_000_000)):
    """Kelly-style live sizing panel (Phase 5): derived entirely from the
    user's own logged open legs and a capital figure they supply — no
    broker call, no fabricated default (a wrong hardcoded capital would
    silently mislead the over-allocation warning)."""
    with get_conn() as conn:
        legs = conn.execute(
            "SELECT action, qty, strike, entry_price, sl FROM position_legs WHERE closed_at IS NULL"
        ).fetchall()

    assignment_exposure = 0.0
    premium_collected = 0.0
    managed_sl_risk = 0.0
    for leg in legs:
        notional = leg["strike"] * leg["qty"]
        if leg["action"] == "SELL":
            assignment_exposure += notional
            premium_collected += leg["entry_price"] * leg["qty"]
            if leg["sl"] is not None and leg["sl"] > leg["entry_price"]:
                managed_sl_risk += (leg["sl"] - leg["entry_price"]) * leg["qty"]
        else:
            premium_collected -= leg["entry_price"] * leg["qty"]
            if leg["sl"] is not None and leg["sl"] < leg["entry_price"]:
                managed_sl_risk += (leg["entry_price"] - leg["sl"]) * leg["qty"]

    exposure_pct = round(assignment_exposure / capital * 100, 2)
    sl_pct = round(managed_sl_risk / capital * 100, 2)

    return KellySizingResponse(
        capital=capital,
        assignment_exposure=round(assignment_exposure, 2),
        exposure_pct_of_capital=exposure_pct,
        premium_collected=round(premium_collected, 2),
        managed_sl_risk=round(managed_sl_risk, 2),
        managed_sl_risk_pct_of_capital=sl_pct,
        over_allocated=exposure_pct > OVER_ALLOCATED_PCT,
        open_leg_count=len(legs),
    )
