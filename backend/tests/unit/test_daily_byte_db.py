"""DB round-trip tests for the daily-byte worker's persistence helpers
(_insert_message / _insert_analysis / _upsert_digest). No network — these run
against the per-test fresh SQLite DB and catch any column/query mismatch."""

import json

from app.db.sqlite import get_conn
from app.models.schemas import DailyByteAnalysis
from app.workers.daily_byte_loop import (
    ANALYSIS_VERSION,
    _insert_analysis,
    _insert_message,
    _upsert_digest,
)


def _message(**overrides) -> dict:
    base = {
        "group_slot": 1,
        "chat_id": 123,
        "chat_username": "niveyshak1",
        "chat_title": "Nivey Shak",
        "message_id": 42,
        "posted_at": "2026-01-01T09:00:00+00:00",
        "posted_local": "2026-01-01T14:30:00+05:30",
        "edit_date": None,
        "is_pinned": 0,
        "is_forward": 0,
        "sender_id": 999,
        "sender_username": "author",
        "sender_name": "Author",
        "is_bot": 0,
        "reply_to_msg_id": None,
        "text": "NIFTY support 24000, RELIANCE long target 2500.",
        "entities_json": json.dumps({"cashtags": []}),
        "media_type": None,
        "media_json": None,
        "views": 10,
        "forwards": 1,
        "reactions_json": json.dumps({}),
        "word_count": 7,
    }
    base.update(overrides)
    return base


def test_message_insert_is_deduped():
    with get_conn() as conn:
        first = _insert_message(conn, _message())
        second = _insert_message(conn, _message())  # same chat_id + message_id
        assert first == second
        count = conn.execute("SELECT COUNT(*) AS n FROM daily_byte_messages").fetchone()["n"]
        assert count == 1


def test_analysis_insert_round_trip():
    analysis = DailyByteAnalysis(
        sentiment="bullish",
        confidence=0.8,
        nifty_support=[24000.0],
        symbols=[{"ticker": "RELIANCE", "direction": "long", "target": 2500.0}],
    )
    with get_conn() as conn:
        fid = _insert_message(conn, _message())
        _insert_analysis(conn, fid, analysis, "Digest text.", True, "gpt-4o-mini")
        row = conn.execute(
            "SELECT sentiment, symbols_json, is_valid, analysis_version FROM daily_byte_analyses WHERE message_fk = ?",
            (fid,),
        ).fetchone()
        assert row["sentiment"] == "bullish"
        assert row["analysis_version"] == ANALYSIS_VERSION
        assert row["is_valid"] == 1
        assert json.loads(row["symbols_json"])[0]["ticker"] == "RELIANCE"


def test_digest_upsert_replaces_on_same_date():
    with get_conn() as conn:
        _upsert_digest(conn, "2026-01-01", {"market_bias": "bullish", "top_symbols": [], "summary": "A"})
        _upsert_digest(conn, "2026-01-01", {"market_bias": "bearish", "top_symbols": [], "summary": "B"})
        rows = conn.execute("SELECT market_bias, summary FROM daily_byte_digests WHERE date = ?", ("2026-01-01",)).fetchall()
        assert len(rows) == 1
        assert rows[0]["market_bias"] == "bearish"
