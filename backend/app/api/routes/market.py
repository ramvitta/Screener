import json
import logging
from datetime import date, datetime, time, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from app.api.routes.auth import _client, ensure_authenticated
from app.config import settings
from app.db.sqlite import get_conn
from app.integrations import instruments
from app.integrations.definedge_client import DefinedgeAuthError
from app.models.schemas import (
    BreadthResponse,
    BreadthSnapshot,
    DailyByteAnalysis,
    DailyByteDigestResponse,
    DailyByteSource,
    IndexQuote,
)

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


@router.get("/breadth", response_model=BreadthResponse)
async def market_breadth(
    days: int = Query(60, ge=1, le=500),
    weeks: int = Query(52, ge=1, le=520),
    months: int = Query(24, ge=1, le=240),
    hours: int = Query(30, ge=1, le=200),
):
    """Market breadth across four granularities, read from the persisted
    breadth_snapshots table (populated by workers/breadth_loop.py). No broker
    call — this is the cached series, so it is cheap and always available."""

    def load(bucket: str, limit: int) -> List[BreadthSnapshot]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT ts, metrics_json FROM breadth_snapshots WHERE bucket = ? ORDER BY ts DESC LIMIT ?",
                (bucket, limit),
            ).fetchall()
        out = []
        for r in reversed(rows):
            try:
                metrics = json.loads(r["metrics_json"]) if isinstance(r["metrics_json"], str) else (r["metrics_json"] or {})
            except ValueError:
                metrics = {}
            out.append(BreadthSnapshot(bucket=bucket, ts=r["ts"], metrics=metrics))
        return out

    daily = load("day", days)
    total = daily[-1].metrics.get("total") if daily else 0
    as_of = None
    if daily:
        try:
            as_of = datetime.combine(date.fromisoformat(daily[-1].ts[:10]), time.min)
        except ValueError:
            as_of = None
    return BreadthResponse(
        universe=settings.breadth_universe,
        total=total,
        as_of=as_of,
        intraday=load("hour", hours),
        daily=daily,
        weekly=load("week", weeks),
        monthly=load("month", months),
    )


@router.get("/daily-byte", response_model=DailyByteDigestResponse)
async def daily_byte(dt: Optional[date] = None):
    """The Morning Daily Byte for a given day (default: today, IST) — the
    synthesized digest plus the per-source messages and their validated LLM
    analyses. Read from daily_byte_digests / daily_byte_analyses (populated by
    workers/daily_byte_loop.py)."""
    target = dt or datetime.now(IST).date()
    target_str = target.isoformat()

    with get_conn() as conn:
        digest = conn.execute(
            "SELECT summary, market_bias, top_symbols_json FROM daily_byte_digests WHERE date = ? ORDER BY id DESC LIMIT 1",
            (target_str,),
        ).fetchone()
        rows = conn.execute(
            """
            SELECT m.group_slot, m.chat_username, m.chat_title, m.posted_at, m.text,
                   a.summary AS a_summary, a.extracted_json, a.is_valid
            FROM (
                SELECT message_fk, summary, extracted_json, is_valid,
                       ROW_NUMBER() OVER (PARTITION BY message_fk ORDER BY id DESC) AS rn
                FROM daily_byte_analyses
            ) a
            JOIN daily_byte_messages m ON a.message_fk = m.id
            WHERE a.rn = 1
            ORDER BY m.posted_at DESC LIMIT 60
            """,
        ).fetchall()

    top_symbols = json.loads(digest["top_symbols_json"]) if digest and digest["top_symbols_json"] else []

    sources: List[DailyByteSource] = []
    for r in rows:
        posted = None
        try:
            posted = datetime.fromisoformat(r["posted_at"]) if r["posted_at"] else None
        except ValueError:
            posted = None
        # Only include messages posted on the requested day (IST).
        if posted and posted.astimezone(IST).date() != target:
            continue
        analysis = None
        if r["extracted_json"]:
            try:
                analysis = DailyByteAnalysis.model_validate(json.loads(r["extracted_json"]))
            except Exception:  # noqa: BLE001 — stored JSON is untrusted
                analysis = None
        sources.append(
            DailyByteSource(
                group_slot=r["group_slot"],
                chat_username=r["chat_username"],
                chat_title=r["chat_title"],
                posted_at=posted,
                text=r["text"] or "",
                summary=r["a_summary"],
                analysis=analysis,
                is_valid=bool(r["is_valid"]),
            )
        )

    return DailyByteDigestResponse(
        date=target_str,
        market_bias=digest["market_bias"] if digest else None,
        summary=digest["summary"] if digest else None,
        top_symbols=top_symbols,
        sources=sources,
    )
