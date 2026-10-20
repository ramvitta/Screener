"""Trade-call tracker API: archive search, extracted calls, closure tracking,
and on-demand sync/classify/track triggers.

All routes are read-mostly. The three POST trigger routes start a background
job and return immediately; poll ``/status`` for progress. Manual closure is
the only route that writes a trade-call row directly, and it is labelled
``status_source='manual'`` so it is auditable.
"""

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.api.routes.auth import _client, ensure_authenticated
from app.api.routes.charts import VALID_INTERVALS, _resample
from app.db.sqlite import get_conn
from app.integrations.definedge_client import DefinedgeAuthError
from app.integrations.instruments import ContractNotFound, find_contract_for_call, get_master_rows
from app.models.schemas import (
    TelegramArchiveMessage,
    TelegramCall,
    TelegramCallDetail,
    TelegramCallEvent,
    TelegramCallStats,
    TelegramChatSummary,
    TelegramClassifyRequest,
    TelegramCloseRequest,
    TelegramSyncRequest,
)
from app.workers.telegram_calls_loop import (
    get_classify_status,
    get_sync_status,
    get_track_status,
    run_classify_job,
    run_sync_job,
    run_tracking_job,
)

router = APIRouter(prefix="/api/v1/telegram", tags=["telegram-calls"])
IST = ZoneInfo("Asia/Kolkata")
CHART_LOOKBACK_DAYS = 2


def _source_url(row: dict) -> Optional[str]:
    username = row.get("chat_username")
    return f"https://t.me/{username}/{row['message_id']}" if username else None


def _call_from_row(row: dict) -> TelegramCall:
    data = dict(row)
    data["source_url"] = _source_url(data)
    data["is_valid"] = bool(data.get("is_valid", 1))
    return TelegramCall.model_validate(data)


def _archive_from_row(row: dict) -> TelegramArchiveMessage:
    return TelegramArchiveMessage.model_validate(dict(row))


def _parse_json(value) -> Optional[dict]:
    if not value:
        return None
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        return None


_CALL_FILTERS = """
    SELECT c.*, m.text AS source_text
    FROM telegram_calls c
    JOIN daily_byte_messages m ON m.id = c.message_fk
    LEFT JOIN telegram_message_classifications cl ON cl.message_fk = c.message_fk
    WHERE 1 = 1
"""


@router.get("/calls")
def list_calls(
    status: Optional[str] = Query(None, description="open|target_hit|sl_hit|closed|expired"),
    symbol: Optional[str] = None,
    chat_id: Optional[int] = None,
    category: Optional[str] = None,
    direction: Optional[str] = Query(None, description="long|short"),
    segment: Optional[str] = None,
    status_source: Optional[str] = Query(None, description="message|price|manual"),
    q: Optional[str] = Query(None, max_length=120),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    sql = _CALL_FILTERS
    params: list = []
    if status:
        sql += " AND c.status = ?"
        params.append(status)
    if symbol:
        sql += " AND c.symbol = ?"
        params.append(symbol.upper())
    if chat_id is not None:
        sql += " AND c.chat_id = ?"
        params.append(chat_id)
    if category:
        sql += " AND cl.category = ?"
        params.append(category)
    if direction:
        sql += " AND c.direction = ?"
        params.append(direction)
    if segment:
        sql += " AND c.segment = ?"
        params.append(segment)
    if status_source:
        sql += " AND c.status_source = ?"
        params.append(status_source)
    if q:
        sql += " AND (c.symbol LIKE ? OR c.display_name LIKE ? OR m.text LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like])
    if date_from:
        sql += " AND date(c.posted_at) >= ?"
        params.append(date_from.isoformat())
    if date_to:
        sql += " AND date(c.posted_at) <= ?"
        params.append(date_to.isoformat())

    count_sql = "SELECT COUNT(*) FROM (" + sql + ") x"
    with get_conn() as conn:
        total = conn.execute(count_sql, params).fetchone()[0]
        rows = conn.execute(sql + " ORDER BY c.posted_at DESC, c.id DESC LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
    return {"items": [_call_from_row(dict(r)) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/calls/{call_id}", response_model=TelegramCallDetail)
def get_call(call_id: int) -> TelegramCallDetail:
    with get_conn() as conn:
        row = conn.execute(
            _CALL_FILTERS + " AND c.id = ?", (call_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="call not found")
        base = _call_from_row(dict(row))

        event_rows = conn.execute(
            """
            SELECT e.*, m.text
            FROM telegram_call_events e
            JOIN daily_byte_messages m ON m.id = e.message_fk
            WHERE e.call_id = ?
            ORDER BY e.posted_at ASC, e.id ASC
            """,
            (call_id,),
        ).fetchall()
        events = [
            TelegramCallEvent.model_validate({**dict(r), "is_valid": bool(r["is_valid"])})
            for r in event_rows
        ]

        event_ids = [r["message_fk"] for r in event_rows]
        extra = ""
        extra_params: list = []
        if event_ids:
            extra = " OR id IN (" + ",".join("?" for _ in event_ids) + ")"
            extra_params = event_ids
        thread_rows = conn.execute(
            f"""
            SELECT m.*, cl.category, cl.method, cl.confidence,
                   (SELECT COUNT(*) FROM telegram_calls c2 WHERE c2.message_fk = m.id) AS call_count
            FROM daily_byte_messages m
            LEFT JOIN telegram_message_classifications cl ON cl.message_fk = m.id
            WHERE m.chat_id = ? AND (m.message_id = ? OR m.reply_to_msg_id = ? {extra})
            ORDER BY m.posted_at ASC, m.id ASC
            LIMIT 200
            """,
            (base.chat_id, base.message_id, base.message_id, *extra_params),
        ).fetchall()
        thread = [_archive_from_row(dict(r)) for r in thread_rows]

    detail = TelegramCallDetail(**base.model_dump(), extraction=_parse_json(row["extraction_json"]), events=events, thread=thread)
    return detail


def _posted_ist(value) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def _chart_window(call: dict) -> tuple[datetime, datetime]:
    """A few sessions around the call, clamped to the present. Multi-day
    windows give the entry/SL/target levels useful context instead of a chart
    that starts exactly at the alert and has nowhere to draw them. Commodities
    use MCX hours (09:00–23:30), everything else NSE hours (09:15–15:30)."""
    commodity = str(call.get("segment") or "").startswith("commodity")
    open_time = time(9, 0) if commodity else time(9, 15)
    close_time = time(23, 30) if commodity else time(15, 30)
    posted = _posted_ist(call["posted_at"])
    start = datetime.combine(posted.date() - timedelta(days=CHART_LOOKBACK_DAYS), open_time)
    end = min(datetime.now(IST).replace(tzinfo=None), datetime.combine(posted.date() + timedelta(days=1), close_time))
    if end <= start:
        end = start + timedelta(days=1)
    return start, end


def _call_label(call: dict) -> str:
    if call.get("option_type") and call.get("strike") is not None:
        strike = float(call["strike"])
        strike_text = str(int(strike)) if strike.is_integer() else f"{strike:g}"
        return f"{call['symbol']} {strike_text} {call['option_type']}"
    return call.get("display_name") or call["symbol"]


def _call_levels(call: dict) -> dict:
    entry = call.get("entry_price")
    if entry is None and call.get("entry_low") is not None and call.get("entry_high") is not None:
        entry = (float(call["entry_low"]) + float(call["entry_high"])) / 2
    return {
        "entry": entry,
        "entry_low": call.get("entry_low"),
        "entry_high": call.get("entry_high"),
        "stop_loss": call.get("stop_loss"),
        "targets": [v for v in (call.get("target_1"), call.get("target_2"), call.get("target_3")) if v is not None],
    }


@router.get("/calls/{call_id}/chart")
async def get_call_chart(call_id: int, interval: int = Query(5, ge=1, le=60)) -> dict:
    """Five-minute (or requested) candles for a call's actual contract, with
    entry / stop-loss / target levels returned as structured data so the
    frontend can draw price lines without re-deriving anything."""
    if interval not in VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"interval must be one of {sorted(VALID_INTERVALS)}")

    with get_conn() as conn:
        row = conn.execute(_CALL_FILTERS + " AND c.id = ?", (call_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="call not found")
        call = dict(row)

    posted = _posted_ist(call["posted_at"])
    expiry = None
    if call.get("expiry"):
        try:
            expiry = date.fromisoformat(call["expiry"])
        except ValueError:
            expiry = None

    try:
        await ensure_authenticated()
        master = await get_master_rows(_client)
        contract = find_contract_for_call(
            master,
            symbol=call["symbol"],
            segment=call["segment"],
            expiry=expiry,
            expiry_text=call.get("expiry_text"),
            strike=call.get("strike"),
            option_type=call.get("option_type"),
            posted=posted.date(),
        )
    except DefinedgeAuthError:
        raise HTTPException(status_code=401, detail="Not authenticated with Definedge") from None
    except ContractNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except Exception as exc:  # noqa: BLE001 — broker outage is a degraded chart, not a crash
        raise HTTPException(status_code=502, detail=f"instrument lookup unavailable ({type(exc).__name__})") from exc

    start, end = _chart_window(call)
    try:
        candles = await _client.get_historical_candles(contract.exchange, contract.token, "minute", start, end)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"candles unavailable ({type(exc).__name__})") from exc

    candles = _resample(candles, interval)
    last = candles[-1] if candles else None
    return {
        "call_id": call_id,
        "label": _call_label(call),
        "trading_symbol": contract.trading_symbol,
        "exchange": contract.exchange,
        "strike": contract.strike,
        "expiry": contract.expiry.isoformat() if contract.expiry else None,
        "segment": call.get("segment"),
        "instrument_type": contract.instrument_type,
        "underlying": contract.underlying or call["symbol"],
        "lot_size": contract.lot_size,
        "tick_size": contract.tick_size,
        "option_type": call.get("option_type"),
        "direction": call.get("direction"),
        "action": call.get("action"),
        "status": call.get("status"),
        "posted_at": posted.isoformat(),
        "posted_at_utc_seconds": int(posted.timestamp()),
        "interval_minutes": interval,
        "as_of": datetime.now(IST).isoformat(),
        "levels": _call_levels(call),
        "ltp": last["close"] if last else None,
        "first_candle_at": candles[0]["ts"] if candles else None,
        "last_candle_at": last["ts"] if last else None,
        "candles": candles,
    }


@router.get("/stats", response_model=TelegramCallStats)
def call_stats() -> TelegramCallStats:
    with get_conn() as conn:
        status_rows = conn.execute("SELECT status, COUNT(*) AS n FROM telegram_calls GROUP BY status").fetchall()
        by_status = {r["status"]: int(r["n"]) for r in status_rows}
        total = sum(by_status.values())
        tracked = conn.execute("SELECT COUNT(*) FROM telegram_calls WHERE tracking_status = 'tracked'").fetchone()[0]
        unresolved = conn.execute("SELECT COUNT(*) FROM telegram_calls WHERE tracking_status = 'unresolved'").fetchone()[0]
        total_messages = conn.execute("SELECT COUNT(*) FROM daily_byte_messages WHERE text IS NOT NULL AND text != ''").fetchone()[0]
        classified = conn.execute("SELECT COUNT(*) FROM telegram_message_classifications WHERE is_valid = 1").fetchone()[0]
        avg = conn.execute(
            "SELECT AVG(realized_pct) FROM telegram_calls WHERE realized_pct IS NOT NULL AND status IN ('target_hit','sl_hit','closed','expired')"
        ).fetchone()[0]
    wins = by_status.get("target_hit", 0) + by_status.get("closed", 0)
    losses = by_status.get("sl_hit", 0)
    win_rate = round(wins / (wins + losses) * 100, 2) if (wins + losses) else None
    sync = get_sync_status()
    last_sync_at = None
    if sync.get("last_run") and sync.get("state") in ("ok", "degraded"):
        try:
            last_sync_at = datetime.fromisoformat(sync["last_run"])
        except ValueError:
            pass
    return TelegramCallStats(
        total_calls=total,
        open=by_status.get("open", 0),
        target_hit=by_status.get("target_hit", 0),
        sl_hit=by_status.get("sl_hit", 0),
        closed=by_status.get("closed", 0),
        expired=by_status.get("expired", 0),
        win_rate=win_rate,
        avg_realized_pct=round(avg, 2) if avg is not None else None,
        tracked=int(tracked),
        unresolved=int(unresolved),
        total_messages=int(total_messages),
        classified_messages=int(classified),
        last_sync_at=last_sync_at,
    )


@router.get("/chats", response_model=List[TelegramChatSummary])
def list_chats() -> List[TelegramChatSummary]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT m.chat_id, MAX(m.chat_username) AS username, MAX(m.chat_title) AS title,
                   COUNT(*) AS message_count, MIN(m.posted_at) AS first_message_at, MAX(m.posted_at) AS last_message_at,
                   COUNT(c.id) AS call_count,
                   SUM(CASE WHEN c.status = 'open' THEN 1 ELSE 0 END) AS open_count,
                   SUM(CASE WHEN c.status = 'target_hit' THEN 1 ELSE 0 END) AS target_hit_count,
                   SUM(CASE WHEN c.status = 'sl_hit' THEN 1 ELSE 0 END) AS sl_hit_count,
                   SUM(CASE WHEN c.status = 'closed' THEN 1 ELSE 0 END) AS closed_count
            FROM daily_byte_messages m
            LEFT JOIN telegram_calls c ON c.message_fk = m.id
            GROUP BY m.chat_id
            ORDER BY message_count DESC
            """
        ).fetchall()
    return [TelegramChatSummary.model_validate(dict(r)) for r in rows]


@router.get("/messages")
def list_messages(
    chat_id: Optional[int] = None,
    category: Optional[str] = None,
    q: Optional[str] = Query(None, max_length=120),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Searchable raw archive — the 'go back in time' view."""
    where = "WHERE m.text IS NOT NULL AND m.text != ''"
    params: list = []
    if chat_id is not None:
        where += " AND m.chat_id = ?"
        params.append(chat_id)
    if category:
        where += " AND cl.category = ?"
        params.append(category)
    if q:
        where += " AND m.text LIKE ?"
        params.append(f"%{q}%")
    if date_from:
        where += " AND date(m.posted_at) >= ?"
        params.append(date_from.isoformat())
    if date_to:
        where += " AND date(m.posted_at) <= ?"
        params.append(date_to.isoformat())

    base = (
        "FROM daily_byte_messages m LEFT JOIN telegram_message_classifications cl ON cl.message_fk = m.id "
        + where
    )
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) " + base, params).fetchone()[0]
        rows = conn.execute(
            """
            SELECT m.*, cl.category, cl.method, cl.confidence,
                   (SELECT COUNT(*) FROM telegram_calls c WHERE c.message_fk = m.id) AS call_count
            """ + base + " ORDER BY m.posted_at DESC, m.id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    return {"items": [_archive_from_row(dict(r)) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/status")
def sync_status() -> dict:
    return {"sync": get_sync_status(), "classify": get_classify_status(), "track": get_track_status()}


@router.post("/sync")
async def trigger_sync(req: TelegramSyncRequest, background: BackgroundTasks) -> dict:
    background.add_task(
        run_sync_job,
        req.max_per_chat,
        req.use_llm,
        full=req.full,
        classify=req.classify,
        since=req.since,
    )
    mode = "full-history" if req.full else "incremental"
    return {
        "state": "queued",
        "message": f"Telegram {mode} sync queued",
        "status": get_sync_status(),
    }


@router.post("/classify")
async def trigger_classify(req: TelegramClassifyRequest, background: BackgroundTasks) -> dict:
    background.add_task(run_classify_job, req.limit, req.use_llm, req.only_unclassified)
    return {"state": "queued", "message": "classification queued", "status": get_classify_status()}


@router.post("/track")
async def trigger_track(background: BackgroundTasks, limit: Optional[int] = Query(None, ge=1, le=200)) -> dict:
    background.add_task(run_tracking_job, limit)
    return {"state": "queued", "message": "price tracking queued", "status": get_track_status()}


@router.post("/calls/{call_id}/close", response_model=TelegramCall)
def close_call(call_id: int, req: TelegramCloseRequest) -> TelegramCall:
    with get_conn() as conn:
        row = conn.execute(_CALL_FILTERS + " AND c.id = ?", (call_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="call not found")
        call = dict(row)
        entry = call.get("entry_price") or call.get("entry_low") or call.get("entry_high")
        pct = None
        if entry:
            pct = ((entry - req.exit_price) / entry * 100) if call["direction"] == "short" else ((req.exit_price - entry) / entry * 100)
        r = None
        if entry and call.get("stop_loss"):
            risk = abs(entry - call["stop_loss"])
            if risk > 0:
                pnl = (req.exit_price - entry) if call["direction"] != "short" else (entry - req.exit_price)
                r = round(pnl / risk, 3)
        closed_at = (req.closed_at or datetime.now(timezone.utc)).isoformat()
        conn.execute(
            """
            UPDATE telegram_calls SET status = 'closed', status_source = 'manual', status_reason = ?,
                exit_price = ?, closed_at = ?, realized_pct = ?, r_multiple = ?,
                notes = COALESCE(?, notes), updated_at = datetime('now')
            WHERE id = ?
            """,
            ("manually closed", req.exit_price, closed_at, round(pct, 4) if pct is not None else None, r, req.notes, call_id),
        )
        updated = conn.execute(_CALL_FILTERS + " AND c.id = ?", (call_id,)).fetchone()
    return _call_from_row(dict(updated))
