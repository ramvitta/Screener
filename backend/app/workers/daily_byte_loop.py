"""Morning Daily Byte worker.

Once a day (cron at telegram_daily_byte_hour:minute, IST) it pulls the latest
messages from the configured Telegram channels/groups via Telethon, stores them
raw (append-only, deduped), runs the optional LLM analysis (structured
extraction + summary), and synthesizes a day-level digest.

Degraded behaviour: when Telegram is disabled/unconfigured or no messages are
found, it records status and stores nothing — never fabricates content.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.sqlite import get_conn
from app.integrations.telegram_client import TelegramAuthError, TelegramDailyByteClient
from app.reasoning.byte_analyzer import analyze_messages, synthesize_digest

logger = logging.getLogger(__name__)

ANALYSIS_VERSION = "v2"

_status: dict = {"state": "starting", "message": "daily-byte has not run yet", "last_run": None}


def get_status() -> dict:
    return dict(_status)


def _set_status(state: str, message: str) -> None:
    _status.update(state=state, message=message, last_run=datetime.now(timezone.utc).isoformat())


def _chats() -> List[str]:
    return [c.strip() for c in settings.telegram_daily_byte_chats.split(",") if c.strip()]


def _model() -> str:
    return settings.llm_byte_model or settings.llm_reason_model


def _insert_message(conn, m: dict) -> Optional[int]:
    """Upsert one raw message; returns its row id (existing or new)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO daily_byte_messages
        (group_slot, chat_id, chat_username, chat_title, message_id, posted_at, posted_local, edit_date,
         is_pinned, is_forward, sender_id, sender_username, sender_name, is_bot, reply_to_msg_id,
         text, entities_json, media_type, media_json, views, forwards, reactions_json, word_count, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            m["group_slot"], m["chat_id"], m["chat_username"], m["chat_title"], m["message_id"],
            m["posted_at"], m["posted_local"], m["edit_date"], m["is_pinned"], m["is_forward"],
            m["sender_id"], m["sender_username"], m["sender_name"], m["is_bot"], m["reply_to_msg_id"],
            m["text"], m["entities_json"], m["media_type"], m["media_json"], m["views"], m["forwards"],
            m["reactions_json"], m["word_count"],
        ),
    )
    row = conn.execute(
        "SELECT id FROM daily_byte_messages WHERE chat_id = ? AND message_id = ?", (m["chat_id"], m["message_id"])
    ).fetchone()
    return row["id"] if row else None


def _insert_analysis(conn, message_fk: int, analysis, summary: Optional[str], is_valid: bool, model: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO daily_byte_analyses
        (message_fk, analysis_version, model, extracted_json, summary, sentiment, symbols_json, events_json,
         confidence, is_valid, analyzed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            message_fk,
            ANALYSIS_VERSION,
            model,
            analysis.model_dump_json(),
            summary,
            analysis.sentiment,
            json.dumps([s.model_dump() for s in analysis.symbols]),
            json.dumps([e.model_dump() for e in analysis.events]),
            analysis.confidence,
            int(is_valid),
        ),
    )


def _upsert_digest(conn, date_str: str, digest: dict) -> None:
    conn.execute(
        """
        INSERT INTO daily_byte_digests (date, digest_json, summary, market_bias, top_symbols_json, created_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(date) DO UPDATE SET
            digest_json = excluded.digest_json,
            summary = excluded.summary,
            market_bias = excluded.market_bias,
            top_symbols_json = excluded.top_symbols_json
        """,
        (
            date_str,
            json.dumps(digest),
            digest.get("summary"),
            digest.get("market_bias"),
            json.dumps(digest.get("top_symbols", [])),
        ),
    )


async def _fetch(client: TelegramDailyByteClient, since: Optional[datetime], limit: int) -> List[dict]:
    """Resolve chats and fetch their recent messages, tagging group_slot by
    config order (1-based)."""
    refs = _chats()
    chats = await client.resolve_chats(refs)
    messages: List[dict] = []
    for idx, chat in enumerate(chats):
        chat["group_slot"] = idx + 1
        try:
            fetched = await client.fetch_recent_messages(chat, since, limit)
        except Exception as exc:  # noqa: BLE001 — one chat failing shouldn't abort the run
            logger.warning("daily-byte: fetch failed for %s (%s)", chat["ref"], type(exc).__name__)
            continue
        messages.extend(fetched)
    return messages


async def run_daily_byte_job() -> None:
    if not settings.telegram_enabled:
        return
    refs = _chats()
    if not refs:
        _set_status("degraded", "TELEGRAM_DAILY_BYTE_CHATS is empty — nothing to fetch")
        return

    lookback = timedelta(hours=settings.telegram_history_lookback_hours)
    since = datetime.now(timezone.utc) - lookback
    model = _model()

    client = TelegramDailyByteClient()
    try:
        await client.ensure_session()
        messages = await _fetch(client, since, limit=50)
    except TelegramAuthError as exc:
        _set_status("degraded", f"Telegram not authorized ({exc})")
        logger.warning("daily-byte degraded: %s", exc)
        return
    except Exception as exc:  # noqa: BLE001 — session/connect failure
        _set_status("degraded", f"Telegram unavailable ({type(exc).__name__})")
        logger.warning("daily-byte degraded: %s", type(exc).__name__)
        return
    finally:
        await client.disconnect()

    if not messages:
        _set_status("degraded", "No messages found in the configured chats for the lookback window")
        return

    # Persist raw messages first (source of truth), then analyse.
    pairs: List[tuple] = []  # (message_fk, message_dict)
    with get_conn() as conn:
        for m in messages:
            pairs.append((_insert_message(conn, m), m))

    text_pairs = [(fid, m) for fid, m in pairs if m["text"]][: settings.llm_byte_max_messages]
    texts = [m["text"] for _, m in text_pairs]
    results = await analyze_messages(texts, model)  # length == len(text_pairs)

    with get_conn() as conn:
        for (fid, _m), (analysis, summary, is_valid) in zip(text_pairs, results, strict=True):
            if fid is None:
                continue
            _insert_analysis(conn, fid, analysis, summary, is_valid, model)

        digest = synthesize_digest(
            [{"analysis": a, "summary": s, "is_valid": v} for (a, s, v) in results]
        )
        ist = ZoneInfo(settings.telegram_daily_byte_timezone or "Asia/Kolkata")
        date_str = datetime.now(ist).date().isoformat()
        _upsert_digest(conn, date_str, digest)

    _set_status("ok", f"{len(messages)} daily-byte messages from {len(refs)} chats")
    logger.info("daily-byte: stored %d messages, analysed %d", len(messages), len(results))


async def run_daily_byte_backfill(limit: int = 200) -> None:
    """Seed history: fetch a larger window without a `since` filter (no LLM
    analysis) so the raw archive can be backfilled in one go."""
    if not settings.telegram_enabled or not _chats():
        return
    client = TelegramDailyByteClient()
    try:
        await client.ensure_session()
        messages = await _fetch(client, since=None, limit=limit)
    except (TelegramAuthError, Exception) as exc:  # noqa: BLE001
        logger.warning("daily-byte backfill failed (%s)", type(exc).__name__)
        return
    finally:
        await client.disconnect()
    with get_conn() as conn:
        for m in messages:
            _insert_message(conn, m)
    logger.info("daily-byte backfill: stored %d messages", len(messages))
