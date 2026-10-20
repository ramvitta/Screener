"""Telegram trade-call tracker.

Three cooperating jobs, all idempotent:

* ``run_sync_job`` — backfill/fetch raw Telegram history into
  ``daily_byte_messages`` (shared with the Daily Byte), then classify each
  message with the deterministic parser and optional LLM enrichment.
* ``run_classify_job`` — classify already-stored messages (used after a manual
  sync, or to re-run a new parser/prompt version).
* ``run_tracking_job`` — mark open calls to closure using follow-up messages
  where available and Definedge daily candles otherwise.

The message archive is the source of truth. A call is only ever stored with
numbers that were present in the message (see call_analyzer's faithfulness
check); price tracking is explicitly labelled ``status_source='price'`` so a
user can tell a channel-confirmed closure from an inferred one.
"""

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.sqlite import get_conn
from app.integrations.telegram_calls_parser import parse_message
from app.integrations.telegram_client import TelegramAuthError, TelegramDailyByteClient
from app.reasoning.call_analyzer import analyze_messages_llm
from app.workers.daily_byte_loop import _chats as _daily_byte_chats
from app.workers.daily_byte_loop import _insert_message

logger = logging.getLogger(__name__)

IST = ZoneInfo(settings.telegram_daily_byte_timezone or "Asia/Kolkata")
CLASSIFIER_VERSION = "v1"
# A call whose contract has expired without a channel/price closure is not a
# win or a loss — the outcome is genuinely unknown. Keep that explicit so it is
# never silently treated as a profitable or losing call.
CONTRACT_EXPIRED_REASON = "Status Unknown - Contract Expired"

_sync_status: dict = {"state": "starting", "message": "trade-call sync has not run yet", "last_run": None, "stats": {}}
_classify_status: dict = {"state": "starting", "message": "trade-call classifier has not run yet", "last_run": None, "stats": {}}
_track_status: dict = {"state": "starting", "message": "trade-call tracking has not run yet", "last_run": None, "stats": {}}
_locks: Dict[str, asyncio.Lock] = {}


def _lock(name: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = f"{name}:{id(loop)}"
    lock = _locks.get(key)
    if lock is None:
        _locks.clear()
        lock = _locks[key] = asyncio.Lock()
    return lock


def _set(status: dict, state: str, message: str, **stats) -> None:
    status.update(state=state, message=message, last_run=datetime.now(timezone.utc).isoformat(), stats=stats)


def get_sync_status() -> dict:
    return dict(_sync_status)


def get_classify_status() -> dict:
    return dict(_classify_status)


def get_track_status() -> dict:
    return dict(_track_status)


def _parse_posted(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _call_expiry(call: dict) -> Optional[date]:
    value = call.get("expiry")
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _today_ist() -> date:
    return datetime.now(IST).date()


def _contract_expired(expiry: Optional[date]) -> bool:
    """True when the contract's expiry is strictly before today (IST)."""
    return expiry is not None and expiry < _today_ist()


def _expired_values() -> dict:
    """Closure values for an expired contract with an unknown outcome.

    Deliberately leaves exit_price/realized_pct/r_multiple unset: expiry tells
    us the call can no longer be traded, not whether it made money.
    """
    return {
        "status": "expired",
        "status_reason": CONTRACT_EXPIRED_REASON,
        "status_source": "price",
        "exit_price": None,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "realized_pct": None,
        "r_multiple": None,
        "tracking_status": "contract_expired",
        "tracking_message": CONTRACT_EXPIRED_REASON,
    }


def _expire_known_contracts() -> int:
    """Bulk-close every still-open call whose known contract expiry has passed.

    This is intentionally not limited by the tracking batch: the oldest open
    calls would otherwise never be reached because tracking always starts with
    the newest batch.
    """
    expired = 0
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, expiry FROM telegram_calls
            WHERE status = 'open' AND expiry IS NOT NULL AND TRIM(expiry) != ''
            """
        ).fetchall()
        for row in rows:
            expiry = _call_expiry(dict(row))
            if _contract_expired(expiry):
                _update_tracking(conn, row["id"], _expired_values())
                expired += 1
    return expired


def _model() -> str:
    return settings.llm_calls_model or settings.llm_byte_extract_model or settings.llm_byte_model or settings.llm_reason_model


def _message_row(conn, message_fk: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM daily_byte_messages WHERE id = ?", (message_fk,)).fetchone()
    return dict(row) if row else None


def _upsert_classification(conn, message_fk: int, extraction, method: str, is_valid: bool) -> None:
    conn.execute(
        """
        INSERT INTO telegram_message_classifications
        (message_fk, category, method, confidence, is_trade_call, summary, extracted_json, is_valid, classified_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(message_fk) DO UPDATE SET
            category = excluded.category,
            method = excluded.method,
            confidence = excluded.confidence,
            is_trade_call = excluded.is_trade_call,
            summary = excluded.summary,
            extracted_json = excluded.extracted_json,
            is_valid = excluded.is_valid,
            classified_at = excluded.classified_at
        """,
        (
            message_fk,
            extraction.category,
            method,
            extraction.confidence,
            int(extraction.is_trade_call),
            extraction.summary,
            extraction.model_dump_json(),
            int(is_valid),
        ),
    )


def _upsert_call(conn, message: dict, call, index: int, method: str, is_valid: bool, confidence: Optional[float] = None) -> int:
    conn.execute(
        """
        INSERT INTO telegram_calls
        (message_fk, chat_id, chat_username, chat_title, message_id, posted_at, call_index,
         symbol, symbol_raw, display_name, segment, exchange, direction, action, option_type, strike,
         expiry, expiry_text, entry_low, entry_high, entry_price, stop_loss, target_1, target_2, target_3,
         cmp, horizon, confidence, method, is_valid, extraction_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(message_fk, call_index) DO UPDATE SET
            symbol = excluded.symbol,
            symbol_raw = excluded.symbol_raw,
            display_name = excluded.display_name,
            segment = excluded.segment,
            exchange = excluded.exchange,
            direction = excluded.direction,
            action = excluded.action,
            option_type = excluded.option_type,
            strike = excluded.strike,
            expiry = excluded.expiry,
            expiry_text = excluded.expiry_text,
            entry_low = excluded.entry_low,
            entry_high = excluded.entry_high,
            entry_price = excluded.entry_price,
            stop_loss = excluded.stop_loss,
            target_1 = excluded.target_1,
            target_2 = excluded.target_2,
            target_3 = excluded.target_3,
            cmp = excluded.cmp,
            horizon = excluded.horizon,
            confidence = excluded.confidence,
            method = excluded.method,
            is_valid = excluded.is_valid,
            extraction_json = excluded.extraction_json,
            updated_at = datetime('now')
        """,
        (
            message["id"], message["chat_id"], message.get("chat_username"), message.get("chat_title"),
            message["message_id"], message["posted_at"], index,
            call.symbol, call.symbol_raw, call.display_name, call.segment, call.exchange, call.direction,
            call.action, call.option_type, call.strike, call.expiry, call.expiry_text, call.entry_low,
            call.entry_high, call.entry_price, call.stop_loss, call.target_1, call.target_2, call.target_3,
            call.cmp, call.horizon, confidence, method,
            int(is_valid), call.model_dump_json(),
        ),
    )
    row = conn.execute(
        "SELECT id FROM telegram_calls WHERE message_fk = ? AND call_index = ?", (message["id"], index)
    ).fetchone()
    return int(row["id"])


def _ist_day(value: Optional[str]) -> Optional[date]:
    try:
        return _parse_posted(value or "").astimezone(IST).date()
    except (TypeError, ValueError):
        return None


def _option_identity(row: dict) -> Tuple[str, Optional[float], str]:
    option_type = (row.get("option_type") or "").upper()
    strike = row.get("strike")
    try:
        strike_value = round(float(strike), 4) if strike is not None else None
    except (TypeError, ValueError):
        strike_value = None
    expiry = str(row.get("expiry") or "")[:10]
    return option_type, strike_value, expiry


_MONTH_CONTRACT_RE = re.compile(r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{0,4}$", re.I)


def _is_broad_contractless(symbol: Optional[str], segment: Optional[str], option_type: Optional[str]) -> bool:
    """Contract-less rows that may be compared across days.

    Commodity futures (COPPER BUY) genuinely repeat on different days, so a
    plain symbol+action key would collapse years of history. These are instead
    treated as one live contract per direction. A few channels encode the
    contract month in the symbol itself (``ELECDMBLSEP``, ``ZINCMFEB``); those
    are safe to merge across days too.
    """
    if option_type:
        return False
    if str(segment or "").startswith("commodity"):
        return True
    value = str(symbol or "").upper()
    return len(value) >= 6 and bool(_MONTH_CONTRACT_RE.search(value))


def _same_call_identity(message: dict, call, candidate: dict, *, same_day_only: bool) -> bool:
    """Does `candidate` represent the same call as the newly parsed `call`?

    Options are identified by contract (type + strike + expiry). A contract-less
    commodity future only has symbol/action, so repeated alerts are treated as
    one *live* contract per direction (only open/target-hit rows are compared;
    closed history is untouched). Other contract-less calls are only merged
    within the same IST day, because a new equity idea on a later day is a new
    call rather than a repost.
    """
    if str(candidate.get("symbol") or "").upper() != str(call.symbol or "").upper():
        return False
    if candidate.get("action") != call.action or candidate.get("direction") != call.direction:
        return False
    option_type = (call.option_type or "").upper()
    candidate_type = (candidate.get("option_type") or "").upper()
    if option_type or candidate_type:
        if option_type != candidate_type or call.strike is None or candidate.get("strike") is None:
            return False
        if abs(float(call.strike) - float(candidate["strike"])) >= 0.001:
            return False
        call_expiry = str(call.expiry or "")[:10]
        candidate_expiry = str(candidate.get("expiry") or "")[:10]
        # Expiry is part of an option's identity. When either side is missing,
        # only merge within the same day: a stale blank-expiry row must not
        # swallow a genuinely new contract at the same strike weeks later.
        if call_expiry and candidate_expiry:
            return call_expiry == candidate_expiry
        return _ist_day(message.get("posted_at")) == _ist_day(candidate.get("posted_at"))
    call_expiry = str(call.expiry or "")[:10]
    candidate_expiry = str(candidate.get("expiry") or "")[:10]
    if call_expiry or candidate_expiry:
        if call_expiry and candidate_expiry:
            return call_expiry == candidate_expiry
        return _ist_day(message.get("posted_at")) == _ist_day(candidate.get("posted_at"))
    if same_day_only:
        return _ist_day(message.get("posted_at")) == _ist_day(candidate.get("posted_at"))
    return _is_broad_contractless(call.symbol, call.segment, call.option_type)


def _same_row_identity(left: dict, right: dict, *, same_day_only: bool) -> bool:
    if str(left.get("symbol") or "").upper() != str(right.get("symbol") or "").upper():
        return False
    if left.get("action") != right.get("action") or left.get("direction") != right.get("direction"):
        return False
    left_type, left_strike, left_expiry = _option_identity(left)
    right_type, right_strike, right_expiry = _option_identity(right)
    if left_type or right_type:
        if left_type != right_type or left_strike is None or right_strike is None:
            return False
        if abs(left_strike - right_strike) >= 0.001:
            return False
        if left_expiry and right_expiry:
            return left_expiry == right_expiry
        return _ist_day(left.get("posted_at")) == _ist_day(right.get("posted_at"))
    left_expiry = str(left.get("expiry") or "")[:10]
    right_expiry = str(right.get("expiry") or "")[:10]
    if left_expiry or right_expiry:
        if left_expiry and right_expiry:
            return left_expiry == right_expiry
        return _ist_day(left.get("posted_at")) == _ist_day(right.get("posted_at"))
    if same_day_only:
        return _ist_day(left.get("posted_at")) == _ist_day(right.get("posted_at"))
    return _is_broad_contractless(left.get("symbol"), left.get("segment"), left.get("option_type")) and _is_broad_contractless(
        right.get("symbol"), right.get("segment"), right.get("option_type")
    )


def _active_candidates(conn, message: dict, call) -> List[dict]:
    rows = conn.execute(
        """
        SELECT * FROM telegram_calls
        WHERE chat_id = ? AND symbol = ? AND action = ? AND direction = ?
          AND status IN ('open', 'target_hit')
        ORDER BY posted_at DESC, id DESC
        """,
        (message["chat_id"], call.symbol, call.action, call.direction),
    ).fetchall()
    return [dict(row) for row in rows]


def _find_active_duplicate(conn, message: dict, call) -> Optional[dict]:
    same_day_only = not _is_broad_contractless(call.symbol, call.segment, call.option_type)
    matches = [
        candidate
        for candidate in _active_candidates(conn, message, call)
        if _same_call_identity(message, call, candidate, same_day_only=same_day_only)
    ]
    if not matches:
        return None
    # A closed/target-hit row is the strongest identity: subsequent reposts of
    # the same contract should attach to the outcome, not reopen it as a new
    # call and double-count the win.
    terminal = [row for row in matches if row.get("status") == "target_hit"]
    pool = terminal or matches
    return max(pool, key=lambda row: (row.get("posted_at") or "", row.get("id") or 0))


def _merge_duplicate_call(conn, canonical: dict, call) -> None:
    """Carry the latest plan levels onto the surviving call.

    Entry is only filled when the canonical row has none: the original entry is
    part of the audit trail.  A repost's newer SL/target/CMP is an update to the
    live plan, so those are allowed to move.
    """
    fields: List[str] = []
    params: List[object] = []
    optional = ("stop_loss", "target_1", "target_2", "target_3", "cmp", "horizon")
    for field in optional:
        value = getattr(call, field, None)
        if value is not None:
            fields.append(f"{field} = ?")
            params.append(value)
    if canonical.get("entry_price") is None:
        for field in ("entry_low", "entry_high", "entry_price"):
            value = getattr(call, field, None)
            if value is not None:
                fields.append(f"{field} = ?")
                params.append(value)
    if not canonical.get("expiry") and call.expiry:
        fields.extend(["expiry = ?", "expiry_text = ?"])
        params.extend([call.expiry, call.expiry_text])
    if call.cmp is not None:
        fields.append("last_price = ?")
        params.append(call.cmp)
    if not fields:
        return
    fields.append("updated_at = datetime('now')")
    params.append(canonical["id"])
    conn.execute(f"UPDATE telegram_calls SET {', '.join(fields)} WHERE id = ?", params)


def _delete_call(conn, call_id: int) -> None:
    conn.execute("UPDATE telegram_call_events SET call_id = NULL WHERE call_id = ?", (call_id,))
    conn.execute("DELETE FROM telegram_calls WHERE id = ?", (call_id,))


def _upsert_or_merge_call(
    conn, message: dict, call, index: int, method: str, is_valid: bool, confidence: Optional[float] = None
) -> int:
    own = conn.execute(
        "SELECT * FROM telegram_calls WHERE message_fk = ? AND call_index = ?", (message["id"], index)
    ).fetchone()
    own_row = dict(own) if own else None
    canonical = _find_active_duplicate(conn, message, call)
    if canonical is not None and (own_row is None or canonical["id"] != own_row["id"]):
        _merge_duplicate_call(conn, canonical, call)
        if own_row is not None:
            _delete_call(conn, int(own_row["id"]))
        return int(canonical["id"])
    return _upsert_call(conn, message, call, index, method, is_valid, confidence)


def _collapse_duplicate_active_calls() -> int:
    """Remove legacy duplicate open/target-hit calls, keeping one survivor.

    This is intentionally separate from the per-message write path: it repairs
    rows created before repost suppression existed.  It only touches the
    derived calls table; the shared message archive is never modified.
    """
    with get_conn() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM telegram_calls WHERE status IN ('open', 'target_hit') ORDER BY posted_at ASC, id ASC"
            ).fetchall()
        ]

    groups: List[List[dict]] = []
    for row in rows:
        for group in groups:
            same_day_only = not (
                _is_broad_contractless(group[0].get("symbol"), group[0].get("segment"), group[0].get("option_type"))
                and _is_broad_contractless(row.get("symbol"), row.get("segment"), row.get("option_type"))
            )
            if _same_row_identity(group[0], row, same_day_only=same_day_only):
                group.append(row)
                break
        else:
            groups.append([row])

    removed = 0
    with get_conn() as conn:
        for group in groups:
            if len(group) < 2:
                continue
            canonical = max(
                group,
                key=lambda row: (1 if row.get("status") == "target_hit" else 0, row.get("posted_at") or "", row.get("id") or 0),
            )
            for row in group:
                if row["id"] != canonical["id"]:
                    _delete_call(conn, int(row["id"]))
                    removed += 1
    return removed


def _same_month(a: Optional[str], b: Optional[str]) -> bool:
    try:
        da, db = date.fromisoformat(a or ""), date.fromisoformat(b or "")
        return (da.year, da.month) == (db.year, db.month)
    except ValueError:
        return False


def _match_open_call(conn, message: dict, event) -> Optional[dict]:
    """Match a follow-up event to the most recent still-open call in the same
    chat. Option details narrow the match; an event without them (e.g. "trade
    closed for DLF at 685.50") falls back to symbol + most recent call."""
    sql = (
        "SELECT * FROM telegram_calls WHERE chat_id = ? AND symbol = ? AND status IN ('open','target_hit') "
        "AND posted_at <= ?"
    )
    params: list = [message["chat_id"], event.symbol, message["posted_at"]]
    if event.option_type:
        sql += " AND option_type = ?"
        params.append(event.option_type)
    if event.strike is not None:
        sql += " AND ABS(strike - ?) < 0.001"
        params.append(event.strike)
    sql += " ORDER BY posted_at DESC, id DESC LIMIT 1"
    row = conn.execute(sql, params).fetchone()
    if row is None and event.option_type:
        # Expiry in a closure may be written differently; retry without it.
        row = conn.execute(
            "SELECT * FROM telegram_calls WHERE chat_id = ? AND symbol = ? AND option_type = ? "
            "AND status IN ('open','target_hit') AND posted_at <= ? ORDER BY posted_at DESC, id DESC LIMIT 1",
            (message["chat_id"], event.symbol, event.option_type, message["posted_at"]),
        ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM telegram_calls WHERE chat_id = ? AND symbol = ? AND status IN ('open','target_hit') "
            "AND posted_at <= ? ORDER BY posted_at DESC, id DESC LIMIT 1",
            (message["chat_id"], event.symbol, message["posted_at"]),
        ).fetchone()
    return dict(row) if row else None


def _upsert_event(conn, message: dict, event, index: int, method: str, is_valid: bool, call_id: Optional[int]) -> int:
    conn.execute(
        """
        INSERT INTO telegram_call_events
        (call_id, message_fk, chat_id, message_id, posted_at, event_index, event_type, symbol, option_type,
         strike, expiry, price, target_value, realized_pct, note, method, is_valid, extracted_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(message_fk, event_index) DO UPDATE SET
            call_id = COALESCE(telegram_call_events.call_id, excluded.call_id),
            event_type = excluded.event_type,
            symbol = excluded.symbol,
            option_type = excluded.option_type,
            strike = excluded.strike,
            expiry = excluded.expiry,
            price = excluded.price,
            target_value = excluded.target_value,
            realized_pct = excluded.realized_pct,
            note = excluded.note,
            method = excluded.method,
            is_valid = excluded.is_valid,
            extracted_json = excluded.extracted_json
        """,
        (
            call_id, message["id"], message["chat_id"], message["message_id"], message["posted_at"], index,
            event.event_type, event.symbol, event.option_type, event.strike, event.expiry, event.price,
            event.target_value, event.realized_pct, event.note, method, int(is_valid), event.model_dump_json(),
        ),
    )
    row = conn.execute(
        "SELECT id FROM telegram_call_events WHERE message_fk = ? AND event_index = ?", (message["id"], index)
    ).fetchone()
    return int(row["id"])


def _apply_event(conn, call: dict, event, message: dict) -> None:
    status = call["status"]
    if status in ("closed", "sl_hit", "expired"):
        return
    event_type = event.event_type
    exit_price = event.price
    realized_pct = event.realized_pct
    reason = (event.note or event_type.replace("_", " "))[:500]

    if event_type == "target_hit":
        target = event.target_value or event.price or call.get("target_1")
        exit_price = exit_price if exit_price is not None else target
        status = "target_hit"
        reason = f"target hit ({target})" if target is not None else "target hit"
    elif event_type == "sl_hit":
        exit_price = exit_price if exit_price is not None else call.get("stop_loss")
        status = "sl_hit"
        reason = f"stop loss hit ({call.get('stop_loss')})"
    elif event_type == "closed":
        status = "closed"
        reason = "booked/closed in channel follow-up"
    elif event_type == "partial_exit":
        if exit_price is not None:
            conn.execute(
                "UPDATE telegram_calls SET last_price = ?, last_checked_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
                (exit_price, call["id"]),
            )
        return
    elif event_type == "trail":
        new_sl = event.price
        if new_sl is not None:
            conn.execute(
                "UPDATE telegram_calls SET stop_loss = ?, status_reason = ?, updated_at = datetime('now') WHERE id = ?",
                (new_sl, reason, call["id"]),
            )
        return
    else:  # update
        if exit_price is not None:
            conn.execute(
                "UPDATE telegram_calls SET last_price = ?, last_checked_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
                (exit_price, call["id"]),
            )
        return

    entry = call.get("entry_price") or call.get("entry_low") or call.get("entry_high")
    if realized_pct is None and exit_price is not None and entry:
        if call["direction"] == "short":
            realized_pct = (entry - exit_price) / entry * 100
        else:
            realized_pct = (exit_price - entry) / entry * 100
    r_multiple = None
    if exit_price is not None and entry and call.get("stop_loss"):
        risk = abs(entry - call["stop_loss"])
        if risk > 0:
            pnl = (exit_price - entry) if call["direction"] != "short" else (entry - exit_price)
            r_multiple = round(pnl / risk, 3)

    conn.execute(
        """
        UPDATE telegram_calls SET
            status = ?, status_reason = ?, status_source = 'message', exit_price = ?, closed_at = ?,
            realized_pct = ?, r_multiple = ?, last_price = COALESCE(?, last_price),
            last_checked_at = datetime('now'), tracking_status = 'tracked', tracking_message = NULL,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (
            status, reason, exit_price, message["posted_at"],
            round(realized_pct, 4) if realized_pct is not None else None, r_multiple,
            exit_price, call["id"],
        ),
    )


def _store_extraction(conn, message: dict, extraction, method: str, is_valid: bool) -> Tuple[int, int]:
    _upsert_classification(conn, message["id"], extraction, method, is_valid)
    call_ids: List[int] = []
    for idx, call in enumerate(extraction.calls, start=1):
        call_ids.append(
            _upsert_or_merge_call(conn, message, call, idx, method, is_valid, extraction.confidence)
        )
    # A re-classification can produce fewer calls/events than the previous pass;
    # remove stale rows so a parser tightening actually disappears from the UI.
    for row in conn.execute(
        "SELECT id, call_index FROM telegram_calls WHERE message_fk = ?", (message["id"],)
    ).fetchall():
        if row["call_index"] > len(extraction.calls):
            conn.execute("UPDATE telegram_call_events SET call_id = NULL WHERE call_id = ?", (row["id"],))
            conn.execute("DELETE FROM telegram_calls WHERE id = ?", (row["id"],))
    for row in conn.execute(
        "SELECT id, event_index FROM telegram_call_events WHERE message_fk = ?", (message["id"],)
    ).fetchall():
        if row["event_index"] > len(extraction.events):
            conn.execute("DELETE FROM telegram_call_events WHERE id = ?", (row["id"],))
    event_count = 0
    for idx, event in enumerate(extraction.events, start=1):
        matched = _match_open_call(conn, message, event)
        _upsert_event(conn, message, event, idx, method, is_valid, matched["id"] if matched else None)
        event_count += 1
        if matched:
            _apply_event(conn, matched, event, message)
    return len(call_ids), event_count


async def _classify_rows(rows: List[dict], use_llm: bool = False) -> Tuple[int, int]:
    """Classify a list of daily_byte_messages rows, writing calls/events.

    Deterministic parse runs for every row. When `use_llm` is set, only the
    first `llm_calls_max_messages` rows are sent to the LLM; the rest keep
    their regex result, which keeps a full-history backfill bounded.
    """
    if not rows:
        return 0, 0
    rows = sorted(rows, key=lambda r: (r.get("posted_at") or "", r.get("id") or 0))
    regex_results = {}
    for row in rows:
        regex_results[row["id"]] = parse_message(row.get("text") or "", _parse_posted(row.get("posted_at") or ""))

    llm_results: Dict[int, Tuple[object, bool]] = {}
    if use_llm and settings.llm_calls_enabled and settings.llm_api_key:
        candidates = [r for r in rows if (r.get("text") or "").strip()][: settings.llm_calls_max_messages]
        if candidates:
            results = await analyze_messages_llm(
                [((r.get("text") or ""), _parse_posted(r.get("posted_at") or "")) for r in candidates],
                _model(),
            )
            for row, result in zip(candidates, results, strict=True):
                llm_results[row["id"]] = result

    calls_written = events_written = 0
    with get_conn() as conn:
        for row in rows:
            regex = regex_results[row["id"]]
            extraction = regex
            method = "regex"
            is_valid = True
            llm = llm_results.get(row["id"])
            if llm is not None and llm[0] is not None and llm[1]:
                extraction = llm[0]
                method = "llm+regex"
            calls, events = _store_extraction(conn, row, extraction, method, is_valid)
            calls_written += calls
            events_written += events
    return calls_written, events_written


def _counts(conn) -> dict:
    def scalar(sql: str, params: tuple = ()) -> int:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    return {
        "messages": scalar("SELECT COUNT(*) FROM daily_byte_messages WHERE text IS NOT NULL AND text != ''"),
        "classified": scalar("SELECT COUNT(*) FROM telegram_message_classifications"),
        "calls": scalar("SELECT COUNT(*) FROM telegram_calls"),
        "open_calls": scalar("SELECT COUNT(*) FROM telegram_calls WHERE status = 'open'"),
        "events": scalar("SELECT COUNT(*) FROM telegram_call_events"),
        "unmatched_events": scalar("SELECT COUNT(*) FROM telegram_call_events WHERE call_id IS NULL"),
    }


async def run_classify_job(limit: int = 500, use_llm: bool = False, only_unclassified: bool = True) -> None:
    async with _lock("classify"):
        if _classify_status.get("state") == "running":
            return
        _set(_classify_status, "running", "classifying stored Telegram messages")
        try:
            sql = "SELECT * FROM daily_byte_messages WHERE text IS NOT NULL AND text != ''"
            params: list = []
            if only_unclassified:
                sql += (
                    " AND id NOT IN (SELECT message_fk FROM telegram_message_classifications WHERE is_valid = 1)"
                )
            sql += " ORDER BY posted_at ASC, id ASC LIMIT ?"
            params.append(max(1, min(limit, 20000)))
            with get_conn() as conn:
                rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            calls, events = await _classify_rows(rows, use_llm=use_llm)
            deduped = _collapse_duplicate_active_calls()
            with get_conn() as conn:
                stats = _counts(conn)
            _set(
                _classify_status, "ok", f"classified {len(rows)} messages ({deduped} duplicate calls merged)",
                **stats, classified_now=len(rows), calls_now=calls, events_now=events, deduped_now=deduped,
            )
            logger.info(
                "telegram-calls: classified %d messages (%d calls, %d events, %d duplicates merged)",
                len(rows), calls, events, deduped,
            )
        except Exception as exc:  # noqa: BLE001 — background job must not crash the scheduler
            _set(_classify_status, "error", f"classify failed: {type(exc).__name__}")
            logger.exception("telegram-calls: classify job failed")


def _chats_with_slots() -> List[dict]:
    return [
        {"ref": ref, "group_slot": idx + 1}
        for idx, ref in enumerate([c for c in _daily_byte_chats()])
    ]


async def run_sync_job(
    limit_per_chat: Optional[int] = None,
    use_llm: Optional[bool] = None,
    full: bool = False,
    classify: bool = True,
    since: Optional[date] = None,
) -> None:
    """Fetch Telegram messages, then classify the new/unclassified ones.

    A normal refresh is incremental: it asks Telethon only for messages newer
    than the newest id already in the archive. `full=True` deliberately walks
    the whole history again (the repair path for an older gap); both modes use
    the same dedupe-on-``chat_id + message_id`` write, so neither can create
    duplicate rows. Classification is limited to rows that are new or were
    never successfully classified, keeping refresh latency bounded even when
    the archive already holds tens of thousands of messages.
    """
    async with _lock("sync"):
        if _sync_status.get("state") == "running":
            return
        _set(_sync_status, "running", "fetching Telegram history")
        # Repair legacy reposts even when Telegram itself is unavailable.
        deduped = _collapse_duplicate_active_calls()
        if not settings.telegram_enabled:
            _set(_sync_status, "degraded", "TELEGRAM_ENABLED is false")
            return
        chats = _chats_with_slots()
        if not chats:
            _set(_sync_status, "degraded", "TELEGRAM_DAILY_BYTE_CHATS is empty")
            return

        limit = limit_per_chat if limit_per_chat is not None else settings.telegram_backfill_max_per_chat
        do_llm = settings.telegram_backfill_use_llm if use_llm is None else use_llm
        since_dt = datetime.combine(since, datetime.min.time(), tzinfo=IST) if since else None
        client = TelegramDailyByteClient()
        total_messages = new_messages = 0
        stored_rows: List[dict] = []
        try:
            await client.ensure_session()
            resolved = await client.resolve_chats([c["ref"] for c in chats])
            by_ref = {r["ref"]: r for r in resolved}
            for chat in chats:
                resolved_chat = by_ref.get(chat["ref"])
                if not resolved_chat:
                    continue
                resolved_chat["group_slot"] = chat["group_slot"]
                with get_conn() as conn:
                    latest_row = conn.execute(
                        "SELECT MAX(message_id) FROM daily_byte_messages WHERE chat_id = ?",
                        (resolved_chat["chat_id"],),
                    ).fetchone()
                latest_id = int(latest_row[0] or 0) if latest_row else 0
                min_id = 0 if full or latest_id == 0 else latest_id
                try:
                    fetched = await client.fetch_history(
                        resolved_chat,
                        limit=max(0, limit),
                        batch_size=settings.telegram_backfill_batch_size,
                        min_id=min_id,
                    )
                except Exception as exc:  # noqa: BLE001 — one bad chat shouldn't abort the rest
                    logger.warning("telegram-calls: history fetch failed for %s (%s)", chat["ref"], type(exc).__name__)
                    continue
                if since_dt is not None:
                    fetched = [m for m in fetched if _parse_posted(m.get("posted_at") or "") >= since_dt]
                with get_conn() as conn:
                    for m in fetched:
                        existing = conn.execute(
                            "SELECT id FROM daily_byte_messages WHERE chat_id = ? AND message_id = ?",
                            (m["chat_id"], m["message_id"]),
                        ).fetchone()
                        fid = _insert_message(conn, m)
                        if not fid:
                            continue
                        already_classified = conn.execute(
                            "SELECT 1 FROM telegram_message_classifications WHERE message_fk = ? AND is_valid = 1",
                            (fid,),
                        ).fetchone()
                        if existing is None or not already_classified:
                            row = _message_row(conn, fid)
                            if row:
                                stored_rows.append(row)
                        if existing is None:
                            new_messages += 1
                total_messages += len(fetched)
        except TelegramAuthError as exc:
            _set(_sync_status, "degraded", f"Telegram not authorized ({exc})")
            logger.warning("telegram-calls sync degraded: %s", exc)
            return
        except Exception as exc:  # noqa: BLE001
            _set(_sync_status, "error", f"sync failed: {type(exc).__name__}")
            logger.exception("telegram-calls: sync job failed")
            return
        finally:
            await client.disconnect()

        # Also pick up messages that another worker (the daily-byte job) may
        # have archived without classifying. Without this, a refresh that finds
        # no *new* Telegram ids could leave those rows permanently unclassified.
        backlog_limit = max(200, min(2000, settings.telegram_backfill_batch_size * 5))
        with get_conn() as conn:
            backlog = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT m.* FROM daily_byte_messages m
                    LEFT JOIN telegram_message_classifications c
                      ON c.message_fk = m.id AND c.is_valid = 1
                    WHERE c.id IS NULL AND m.text IS NOT NULL AND TRIM(m.text) <> ''
                    ORDER BY m.posted_at ASC, m.id ASC
                    LIMIT ?
                    """,
                    (backlog_limit,),
                ).fetchall()
            ]
        stored_rows.extend(backlog)

        # Deduplicate by row id (a chat may have been resolved twice, and the
        # backlog query overlaps fetched rows) and only classify rows with text.
        unique = {r["id"]: r for r in stored_rows if (r.get("text") or "").strip()}
        calls, events = await _classify_rows(list(unique.values()), use_llm=do_llm) if classify else (0, 0)
        if classify:
            deduped += _collapse_duplicate_active_calls()
        with get_conn() as conn:
            stats = _counts(conn)
        mode = "full-history messages" if full else "new messages"
        _set(
            _sync_status,
            "ok",
            f"fetched {total_messages} {mode} ({new_messages} new) across {len(chats)} chats; {deduped} duplicate calls merged",
            **stats,
            fetched_now=total_messages,
            new_now=new_messages,
            backlog_now=len(backlog),
            calls_now=calls,
            events_now=events,
            deduped_now=deduped,
            full_now=int(full),
        )
        logger.info(
            "telegram-calls: sync fetched %d messages (%d new), %d calls, %d events, %d duplicates merged",
            total_messages, new_messages, calls, events, deduped,
        )


def _pnl(call: dict, exit_price: Optional[float], realized_pct: Optional[float] = None) -> Tuple[Optional[float], Optional[float]]:
    if realized_pct is not None:
        pct = realized_pct
    else:
        entry = call.get("entry_price") or call.get("entry_low") or call.get("entry_high")
        if exit_price is None or not entry:
            return None, None
        pct = ((entry - exit_price) / entry * 100) if call["direction"] == "short" else ((exit_price - entry) / entry * 100)
    entry = call.get("entry_price") or call.get("entry_low") or call.get("entry_high")
    r_multiple = None
    if exit_price is not None and entry and call.get("stop_loss"):
        risk = abs(entry - call["stop_loss"])
        if risk > 0:
            pnl = (exit_price - entry) if call["direction"] != "short" else (entry - exit_price)
            r_multiple = round(pnl / risk, 3)
    return round(pct, 4), r_multiple


def _evaluate_candles(call: dict, candles: List[dict]) -> dict:
    posted = _parse_posted(call["posted_at"])
    relevant = []
    for candle in candles:
        try:
            ts = datetime.fromisoformat(str(candle["ts"]).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        if ts >= posted - timedelta(minutes=1):
            relevant.append(candle)
    if not relevant:
        return {"tracking_status": "no_data", "tracking_message": "no candles after the call was posted"}
    last = relevant[-1]
    high = max(float(c["high"]) for c in relevant)
    low = min(float(c["low"]) for c in relevant)
    close = float(last["close"])
    if call["status"] == "open" and _contract_expired(_call_expiry(call)):
        return {**_expired_values(), "last_price": close}
    short = call["direction"] == "short"
    sl = call.get("stop_loss")
    target = call.get("target_1")
    target_hit = (low <= target) if (short and target is not None) else (high >= target) if target is not None else False
    sl_hit = (high >= sl) if (short and sl is not None) else (low <= sl) if sl is not None else False

    if target_hit and sl_hit:
        # Both levels were touched inside the same daily bar; use the close to
        # decide which one is more likely to have happened last, and record the
        # ambiguity so it can be reviewed.
        if (short and close <= target) or (not short and close >= target):
            sl_hit = False
        elif (short and close >= sl) or (not short and close <= sl):
            target_hit = False
        else:
            return {"tracking_status": "ambiguous", "tracking_message": "daily bar touched both target and SL", "last_price": close}

    if target_hit:
        pct, r = _pnl(call, target)
        return {
            "status": "target_hit", "status_source": "price", "exit_price": target,
            "closed_at": datetime.now(timezone.utc).isoformat(), "realized_pct": pct, "r_multiple": r,
            "tracking_status": "tracked", "tracking_message": f"target {target} reached (daily high/low)",
            "last_price": close,
        }
    if sl_hit:
        pct, r = _pnl(call, sl)
        return {
            "status": "sl_hit", "status_source": "price", "exit_price": sl,
            "closed_at": datetime.now(timezone.utc).isoformat(), "realized_pct": pct, "r_multiple": r,
            "tracking_status": "tracked", "tracking_message": f"stop {sl} reached (daily high/low)",
            "last_price": close,
        }
    return {"tracking_status": "tracked", "tracking_message": "open", "last_price": close}


def _update_tracking(conn, call_id: int, values: dict) -> None:
    fields: list = []
    params: list = []
    for field in (
        "tracking_status", "tracking_message", "last_price", "status", "status_reason",
        "status_source", "exit_price", "closed_at", "realized_pct", "r_multiple",
    ):
        if field in values:
            fields.append(field)
            params.append(values[field])
    if not fields:
        return
    sets = ", ".join(f"{f} = ?" for f in fields)
    conn.execute(
        f"UPDATE telegram_calls SET {sets}, last_checked_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
        (*params, call_id),
    )


async def run_tracking_job(limit: Optional[int] = None) -> None:
    async with _lock("track"):
        if _track_status.get("state") == "running":
            return
        _set(_track_status, "running", "marking open calls to market")
        from app.api.routes.auth import _client, ensure_authenticated
        from app.integrations.instruments import ContractNotFound, find_contract_for_call, get_master_rows

        # Expiry is known from the call itself, so sweep it before touching the
        # broker. This also works while Definedge is down and closes every past
        # contract, not just the newest tracking batch.
        expired_now = _expire_known_contracts()
        if expired_now:
            logger.info("telegram-calls: marked %d open calls as contract-expired", expired_now)

        batch = max(1, min(limit or settings.telegram_call_track_batch, 200))
        with get_conn() as conn:
            calls = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM telegram_calls WHERE status = 'open' ORDER BY posted_at DESC LIMIT ?", (batch,)
                ).fetchall()
            ]
        if not calls:
            with get_conn() as conn:
                stats = _counts(conn)
            _set(
                _track_status, "ok",
                f"no open calls to track ({expired_now} expired by contract date)",
                **stats, expired_now=expired_now,
            )
            return
        try:
            await ensure_authenticated()
            master = await get_master_rows(_client)
        except Exception as exc:  # noqa: BLE001 — auth/broker outage is a status, not a crash
            _set(_track_status, "degraded", f"Definedge unavailable ({type(exc).__name__})")
            logger.warning("telegram-calls tracking degraded: %s", type(exc).__name__)
            return

        tracked = closed = unresolved = errors = 0
        for call in calls:
            posted = _parse_posted(call["posted_at"])
            expiry = _call_expiry(call)
            if call["status"] == "open" and _contract_expired(expiry):
                with get_conn() as conn:
                    _update_tracking(conn, call["id"], _expired_values())
                expired_now += 1
                closed += 1
                continue
            try:
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
            except ContractNotFound:
                with get_conn() as conn:
                    _update_tracking(conn, call["id"], {"tracking_status": "unresolved", "tracking_message": "contract not in instrument master"})
                unresolved += 1
                continue
            except Exception as exc:  # noqa: BLE001
                with get_conn() as conn:
                    _update_tracking(conn, call["id"], {"tracking_status": "error", "tracking_message": type(exc).__name__})
                errors += 1
                continue

            # The resolved contract can reveal an expiry the parser left blank.
            if call["status"] == "open" and _contract_expired(contract.expiry):
                with get_conn() as conn:
                    _update_tracking(conn, call["id"], _expired_values())
                expired_now += 1
                closed += 1
                continue

            timeframe = "minute" if (datetime.now(timezone.utc) - posted) <= timedelta(days=10) else "day"
            start = posted - timedelta(days=1) if timeframe == "day" else posted
            end = datetime.now(IST) + timedelta(days=1)
            try:
                candles = await _client.get_historical_candles(contract.exchange, contract.token, timeframe, start, end)
            except Exception as exc:  # noqa: BLE001
                with get_conn() as conn:
                    _update_tracking(conn, call["id"], {"tracking_status": "error", "tracking_message": type(exc).__name__})
                errors += 1
                await asyncio.sleep(0.2)
                continue

            values = _evaluate_candles(call, candles)
            with get_conn() as conn:
                _update_tracking(conn, call["id"], values)
            tracked += 1
            if values.get("status") in ("target_hit", "sl_hit", "expired"):
                closed += 1
            await asyncio.sleep(0.15)

        with get_conn() as conn:
            stats = _counts(conn)
        _set(
            _track_status, "ok",
            f"tracked {tracked} of {len(calls)} open calls ({closed} newly closed, {expired_now} contract expiries)",
            **stats, tracked_now=tracked, closed_now=closed, unresolved_now=unresolved, errors_now=errors,
            expired_now=expired_now,
        )
        logger.info(
            "telegram-calls: tracked %d calls (%d closed, %d unresolved, %d errors, %d contract expiries)",
            tracked, closed, unresolved, errors, expired_now,
        )
