"""Phase 4 of the UI gap-closing plan: read-only option chain for the Chain &
Entry tab. Straddle vs strangle is purely a frontend display choice over the
same rows — both come from this one endpoint."""

import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.api.routes.auth import _client, ensure_authenticated
from app.integrations import instruments
from app.integrations.definedge_client import DefinedgeAuthError
from app.models.schemas import ChainLegQuote, ChainResponse, ChainRow, Symbol

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/chain", tags=["chain"])


def _pct_diff(ltp: float, open_: float) -> float:
    return round((ltp - open_) / open_ * 100, 2) if open_ else 0.0


async def _leg_quote(rows, symbol: str, expiry: date, strike: float, option_type: str) -> Optional[ChainLegQuote]:
    try:
        contract = instruments.find_option(rows, symbol, expiry, strike, option_type)
        quote = await _client.get_quote(contract.exchange, contract.token)
        ltp = float(quote["ltp"])
        day_open = float(quote.get("day_open") or ltp)
        return ChainLegQuote(strike=strike, ltp=ltp, day_open=day_open, diff_pct=_pct_diff(ltp, day_open))
    except Exception as exc:  # noqa: BLE001 — contract may not exist at this strike/expiry
        logger.debug("chain: no quote for %s %s %s (%s)", symbol, strike, option_type, type(exc).__name__)
        return None


@router.get("/{symbol}", response_model=ChainResponse)
async def option_chain(symbol: Symbol, expiry: Optional[date] = None, depth: int = Query(5, ge=1, le=10)):
    try:
        await ensure_authenticated()
        rows = await instruments.get_master_rows(_client)
        spot_contract = instruments.find_spot(rows, symbol)
        quote = await _client.get_quote(spot_contract.exchange, spot_contract.token)
        spot = float(quote["ltp"])
        atm_strike = round(spot / instruments.NIFTY_STRIKE_INTERVAL) * instruments.NIFTY_STRIKE_INTERVAL
        resolved_expiry = expiry or instruments.nearest_expiry(rows, symbol, date.today())
    except DefinedgeAuthError:
        raise HTTPException(status_code=503, detail="Broker session unavailable") from None

    chain_rows = []
    for offset in range(-depth, depth + 1):
        strike = atm_strike + offset * instruments.NIFTY_STRIKE_INTERVAL
        ce, pe = await _leg_quote(rows, symbol, resolved_expiry, strike, "CE"), await _leg_quote(
            rows, symbol, resolved_expiry, strike, "PE"
        )
        combined_ltp = combined_open = combined_diff = None
        if ce and pe:
            combined_ltp = round(ce.ltp + pe.ltp, 2)
            combined_open = round(ce.day_open + pe.day_open, 2)
            combined_diff = _pct_diff(combined_ltp, combined_open)
        chain_rows.append(
            ChainRow(
                otm=offset,
                strike=strike,
                ce=ce,
                pe=pe,
                combined_ltp=combined_ltp,
                combined_open=combined_open,
                combined_diff_pct=combined_diff,
            )
        )

    return ChainResponse(
        symbol=symbol,
        expiry=resolved_expiry,
        spot=spot,
        atm_strike=atm_strike,
        updated_at=datetime.now(),
        rows=chain_rows,
    )
