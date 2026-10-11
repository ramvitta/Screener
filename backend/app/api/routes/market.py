import logging
from datetime import datetime, timedelta
from typing import List
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from app.api.routes.auth import _client, ensure_authenticated
from app.integrations import instruments
from app.integrations.definedge_client import DefinedgeAuthError
from app.models.schemas import IndexQuote

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/market", tags=["market"])

IST = ZoneInfo("Asia/Kolkata")


@router.get("/indices", response_model=List[IndexQuote])
async def index_quotes():
    """Live index spot quotes for the TopBar — Phase 1 scope is NIFTY-only
    (see SRS_Architecture.md §1.3), so this returns a single real quote
    rather than the illustrative multi-index mock data the UI used to show.
    change_pct is derived from the last completed day's close, since
    Definedge's quote payload doesn't include a previous-close field."""
    try:
        await ensure_authenticated()
        rows = await instruments.get_master_rows(_client)
        spot = instruments.find_spot(rows, "NIFTY")
        quote = await _client.get_quote(spot.exchange, spot.token)
        ltp = float(quote["ltp"])

        prev_close = None
        try:
            end = datetime.now(IST).replace(tzinfo=None)
            start = end - timedelta(days=10)
            candles = await _client.get_historical_candles(spot.exchange, spot.token, "day", start, end)
            today = datetime.now(IST).date()
            prior = [c for c in candles if datetime.fromisoformat(c["ts"]).date() < today]
            if prior:
                prev_close = prior[-1]["close"]
        except Exception as exc:  # noqa: BLE001 — change_pct falls back to 0.0 below
            logger.warning("index_quotes: previous-close lookup failed (%s)", type(exc).__name__)

        change_pct = round((ltp - prev_close) / prev_close * 100, 2) if prev_close else 0.0
        return [IndexQuote(symbol="NIFTY", spot=ltp, change_pct=change_pct)]
    except DefinedgeAuthError:
        raise HTTPException(status_code=503, detail="Broker session unavailable") from None
