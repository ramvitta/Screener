"""Telethon wrapper for the Morning Daily Byte.

Reads the latest messages from a set of Telegram channels/groups (Telethon
reads *history*, unlike a bot which only sees messages posted after it's
added), normalises them into plain dicts, and persists a session file so a
long-running worker never re-prompts for a phone code.

First-run auth is interactive (phone code + optional 2FA) and lives in a
standalone script — scripts/telegram_login.py — because a background worker
has no terminal to read the code from. After that, the .session file is reused.
"""

import asyncio
import json
import logging
import sqlite3
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from app.config import settings

logger = logging.getLogger(__name__)

IST = ZoneInfo(settings.telegram_daily_byte_timezone or "Asia/Kolkata")


class TelegramAuthError(RuntimeError):
    pass


def _safe_attr(obj, name, default=None):
    try:
        return getattr(obj, name)
    except Exception:  # noqa: BLE001 — telethon attributes vary by message type
        return default


def _extract_entities(msg) -> dict:
    """Rich-text + link entities from a Telethon message, decomposed into
    plain lists the DB can index later (hashtags/cashtags/mentions/urls)."""
    from telethon.tl.types import (
        MessageEntityCashtag,
        MessageEntityEmail,
        MessageEntityHashtag,
        MessageEntityMention,
        MessageEntityMentionName,
        MessageEntityTextUrl,
        MessageEntityUrl,
    )

    entities = _safe_attr(msg, "entities") or []
    text = _safe_attr(msg, "message") or ""
    hashtags, cashtags, mentions, urls = [], [], [], []

    def slice_text(e):
        try:
            return text[e.offset : e.offset + e.length]
        except Exception:  # noqa: BLE001
            return ""

    for e in entities:
        if isinstance(e, MessageEntityHashtag):
            hashtags.append(slice_text(e))
        elif isinstance(e, MessageEntityCashtag):
            cashtags.append(slice_text(e))
        elif isinstance(e, (MessageEntityMention, MessageEntityMentionName)):
            mentions.append(slice_text(e))
        elif isinstance(e, MessageEntityTextUrl):
            urls.append(_safe_attr(e, "url"))
        elif isinstance(e, (MessageEntityUrl, MessageEntityEmail)):
            urls.append(slice_text(e))
    return {"hashtags": hashtags, "cashtags": cashtags, "mentions": mentions, "urls": urls}


def _media_info(msg) -> tuple[Optional[str], Optional[dict]]:
    from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto, MessageMediaWebPage

    media = _safe_attr(msg, "media")
    if media is None:
        return None, None
    if isinstance(media, MessageMediaPhoto):
        return "photo", None
    if isinstance(media, MessageMediaWebPage):
        return "webpage", None
    if isinstance(media, MessageMediaDocument):
        doc = _safe_attr(media, "document")
        info = {}
        if doc is not None:
            for attr in _safe_attr(doc, "attributes") or []:
                name = type(attr).__name__
                if name == "DocumentAttributeFilename":
                    info["filename"] = _safe_attr(attr, "file_name")
                elif name == "DocumentAttributeVideo":
                    info["duration"] = _safe_attr(attr, "duration")
                elif name == "DocumentAttributeAudio":
                    info["duration"] = _safe_attr(attr, "duration")
            info["mime_type"] = _safe_attr(doc, "mime_type")
            info["size"] = _safe_attr(doc, "size")
        return "document", info
    return type(media).__name__, None


def _reactions(msg) -> dict:
    reactions = _safe_attr(msg, "reactions")
    if reactions is None:
        return {}
    out = {}
    for rc in _safe_attr(reactions, "results") or []:
        reaction = _safe_attr(rc, "reaction")
        emoticon = _safe_attr(reaction, "emoticon")
        key = emoticon if emoticon else type(reaction).__name__
        out[key] = _safe_attr(rc, "count", 0)
    return out


def _normalise_message(msg, group_slot: int, chat_id: int, chat_username: Optional[str], chat_title: Optional[str]) -> dict:
    """One raw Telegram message → a plain dict for daily_byte_messages."""
    text = _safe_attr(msg, "message") or ""
    sender = _safe_attr(msg, "sender")
    date = _safe_attr(msg, "date")
    edit = _safe_attr(msg, "edit_date")
    media_type, media_json = _media_info(msg)
    return {
        "group_slot": group_slot,
        "chat_id": chat_id,
        "chat_username": chat_username,
        "chat_title": chat_title,
        "message_id": msg.id,
        "posted_at": date.isoformat() if date else None,
        "posted_local": date.astimezone(IST).isoformat() if date else None,
        "edit_date": edit.isoformat() if edit else None,
        "is_pinned": int(bool(_safe_attr(msg, "pinned"))),
        "is_forward": int(bool(_safe_attr(msg, "forward"))),
        "sender_id": _safe_attr(sender, "id"),
        "sender_username": _safe_attr(sender, "username"),
        "sender_name": _safe_attr(sender, "first_name"),
        "is_bot": int(bool(_safe_attr(sender, "bot"))),
        "reply_to_msg_id": _safe_attr(msg, "reply_to_msg_id"),
        "text": text,
        "entities_json": json.dumps(_extract_entities(msg)),
        "media_type": media_type,
        "media_json": json.dumps(media_json) if media_json else None,
        "views": _safe_attr(msg, "views"),
        "forwards": _safe_attr(msg, "forwards"),
        "reactions_json": json.dumps(_reactions(msg)),
        "word_count": len(text.split()),
    }


class TelegramDailyByteClient:
    """Thin wrapper around a Telethon TelegramClient for the daily byte."""

    def __init__(self):
        self._client = None

    def _build_client(self):
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        if settings.telegram_string_session:
            session = StringSession(settings.telegram_string_session)
        else:
            session = settings.telegram_session_path
        return TelegramClient(session, settings.telegram_api_id, settings.telegram_api_hash)

    async def ensure_session(self) -> None:
        """Connect and verify the session is authorized. Raises
        TelegramAuthError (with a hint to run scripts/telegram_login.py) if
        not — a background worker has no terminal to complete the phone-code
        prompt itself.

        The daily-byte and trade-call workers may connect at the same time.
        Telethon's SQLite session then rejects one of them with a transient
        ``OperationalError``; retrying with a fresh client avoids turning that
        race into a degraded sync.
        """
        if not (settings.telegram_api_id and settings.telegram_api_hash):
            raise TelegramAuthError("TELEGRAM_API_ID / TELEGRAM_API_HASH are not set")
        for attempt in range(5):
            if self._client is None:
                self._client = self._build_client()
            try:
                await self._client.connect()
                break
            except Exception as exc:  # noqa: BLE001
                try:
                    await self._client.disconnect()
                except Exception as cleanup_exc:  # noqa: BLE001 — best-effort cleanup
                    logger.debug("telegram: cleanup after connect failure failed (%s)", type(cleanup_exc).__name__)
                self._client = None
                locked = isinstance(exc, sqlite3.OperationalError) or "database is locked" in str(exc).lower()
                if not locked or attempt == 4:
                    raise TelegramAuthError(f"Telegram connect failed ({type(exc).__name__})") from exc
                await asyncio.sleep(1.0 + attempt)
        if not await self._client.is_user_authorized():
            raise TelegramAuthError("Telegram session not authorized — run `python scripts/telegram_login.py` once")

    async def interactive_login(self) -> dict:
        """One-time interactive login (phone code via stdin) for the CLI script."""
        if not (settings.telegram_api_id and settings.telegram_api_hash):
            raise TelegramAuthError("TELEGRAM_API_ID / TELEGRAM_API_HASH are not set")
        if self._client is None:
            self._client = self._build_client()
        await self._client.start(phone=settings.telegram_phone)  # prompts for code/2FA on stdin
        me = await self._client.get_me()
        return {"id": me.id, "username": me.username, "name": me.first_name}

    async def resolve_chats(self, refs: List[str]) -> List[dict]:
        """Map configured usernames/ids to chat entities (id/title/username)."""
        await self.ensure_session()
        resolved = []
        for ref in refs:
            try:
                entity = await self._client.get_entity(ref.strip())
            except Exception as exc:  # noqa: BLE001
                logger.warning("daily-byte: could not resolve chat %r (%s)", ref, type(exc).__name__)
                continue
            resolved.append(
                {
                    "ref": ref.strip(),
                    "chat_id": entity.id,
                    "username": _safe_attr(entity, "username"),
                    "title": _safe_attr(entity, "title") or _safe_attr(entity, "first_name"),
                }
            )
        return resolved

    async def fetch_recent_messages(self, chat: dict, since: datetime, limit: int = 50) -> List[dict]:
        """Fetch the most recent `limit` messages from one chat, filtered to
        those on/after `since`, normalised to plain dicts."""
        await self.ensure_session()
        entity = await self._client.get_entity(chat["ref"])
        messages = await self._client.get_messages(entity, limit=limit)
        out = []
        for msg in messages:
            if msg is None:
                continue
            if since and _safe_attr(msg, "date") and msg.date < since:
                continue
            out.append(
                _normalise_message(msg, chat["group_slot"], chat["chat_id"], chat["username"], chat["title"])
            )
        return out

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def fetch_history(
        self, chat: dict, limit: int = 0, batch_size: int = 200, min_id: int = 0
    ) -> List[dict]:
        """Paginate backwards through a chat's history (newest first).

        `limit=0` means all available messages. When `min_id` is set, Telethon
        returns only messages newer than that message id, which is what makes
        a refresh incremental: the previous sync's newest id is the lower
        bound, so there is no gap between runs and no full re-download. Messages
        are normalised exactly like the daily byte so both features share
        `daily_byte_messages` and its dedupe guarantee.
        """
        await self.ensure_session()
        entity = await self._client.get_entity(chat["ref"])
        out: List[dict] = []
        offset_id = 0
        while True:
            want = batch_size if not limit else min(batch_size, limit - len(out))
            if want <= 0:
                break
            messages = await self._client.get_messages(
                entity, limit=want, offset_id=offset_id, min_id=min_id
            )
            messages = [m for m in messages if m is not None]
            if not messages:
                break
            for msg in messages:
                out.append(
                    _normalise_message(msg, chat["group_slot"], chat["chat_id"], chat["username"], chat["title"])
                )
            offset_id = messages[-1].id
            if len(messages) < want:
                break
        return out
