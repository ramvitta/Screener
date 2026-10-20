"""Deterministic Telegram trade-call extraction and price-tracking decisions."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.db.sqlite import get_conn
from app.integrations.telegram_calls_parser import parse_message
from app.workers.telegram_calls_loop import (
    CONTRACT_EXPIRED_REASON,
    _collapse_duplicate_active_calls,
    _evaluate_candles,
    _expire_known_contracts,
    _parse_posted,
    _store_extraction,
)

IST = ZoneInfo("Asia/Kolkata")
POSTED = datetime(2026, 10, 19, 10, 0, tzinfo=IST)


def _calls(text: str):
    return parse_message(text, POSTED).calls


def _events(text: str):
    return parse_message(text, POSTED).events


@pytest.mark.parametrize(
    "text",
    [
        "Selling pressure is visible. Do not sell in panic.",
        "Nifty traded at 17.2x forward P/E, an 18% discount to Long Period Average.",
        "TIME TO DOUBLE UP, NOT SELL. This is a rare valuation opportunity.",
        "Nifty 50 closed at 24,100; support at 23,900 and resistance at 24,400.",
        "Market update: FIIs bought equities today.",
    ],
)
def test_commentary_is_not_a_trade(text):
    result = parse_message(text, POSTED)
    assert result.calls == []
    assert result.is_trade_call is False
    assert result.category in {"market_commentary", "other"}


def test_equity_alert_with_entry_range_sl_and_targets():
    [call] = _calls("BUY Adani Ports & SEZ @ 1759.5-1761 SL 1720 TGT 1830/1870")
    assert call.symbol == "ADANIPORTS"
    assert call.segment == "equity"
    assert call.entry_low == 1759.5
    assert call.entry_high == 1761.0
    assert call.entry_price == pytest.approx(1760.25)
    assert call.stop_loss == 1720.0
    assert call.target_1 == 1830.0
    assert call.target_2 == 1870.0


def test_index_option_alert_extracts_contract_levels():
    [call] = _calls("SAHI Trade Alert : 19 Oct\nBUY NIFTY 23000 PE @ 149 SL 118 TGT 200")
    assert call.symbol == "NIFTY"
    assert call.segment == "index_option"
    assert call.option_type == "PE"
    assert call.strike == 23000.0
    assert (call.entry_price, call.stop_loss, call.target_1) == (149.0, 118.0, 200.0)


def test_commodity_option_alert_with_month_and_range_levels():
    [call] = _calls("KCALRT: INTRADAY: BUY MCX NATURALGAS OCT 300 CE BETWEEN 8.60 -8.70 TP 10.10 SL 7.60 (CMP- 8.60)")
    assert call.symbol == "NATURALGAS"
    assert call.segment == "commodity_option"
    assert call.option_type == "CE"
    assert call.strike == 300.0
    assert (call.entry_low, call.entry_high) == (8.6, 8.7)
    assert call.entry_price == pytest.approx(8.65)
    assert call.stop_loss == 7.6
    assert call.target_1 == 10.1


def test_kotak_current_market_price_target_and_stop_loss_are_parsed():
    [call] = _calls(
        "INTRADAY: BUY MCX NATURAL GAS NOV 520 CE\n"
        "- Current Market Price - ₹ 44\n"
        "- Target Price- ₹ 53\n"
        "- Stop Loss Price - ₹ 38\n"
        "Disclaimer - https://bit.ly/KsecDisclaimer"
    )
    assert call.symbol == "NATURALGAS"
    assert call.option_type == "CE"
    assert call.strike == 520.0
    assert call.cmp == 44.0
    assert call.entry_price == 44.0
    assert call.target_1 == 53.0
    assert call.stop_loss == 38.0


def test_abv_and_blw_are_accepted_as_entry_and_stop():
    [call] = _calls("TRADE FOR TOMO\n\nBUY DIVISLAB 9400CE\nABV 188-190\nSL BLW 136")
    assert call.symbol == "DIVISLAB"
    assert call.option_type == "CE"
    assert call.strike == 9400.0
    assert (call.entry_low, call.entry_high) == (188.0, 190.0)
    assert call.stop_loss == 136.0


def test_company_name_is_mapped_to_nse_symbol():
    [call] = _calls("BUY Ather Energy @ 1480-1483 SL 1445 TGT 1545")
    assert call.symbol == "ATHERENERG"


def test_follow_up_events_are_classified():
    assert _events("KCALRT: INTRADAY UPDATE: BUY MCX NATURALGAS OCT 300 CE, TARGET OF 16.70 ACHEIVED")[0].event_type == "target_hit"
    assert _events("KCALRT: INTRADAY UPDATE: BUY MCX CRUDEOIL OCT 8700 PE SL TRADED AT 209")[0].event_type == "sl_hit"
    assert _events("KCALRT: INTRADAY UPDATE: BUY MCX SILVERM OCT 225000 PE BOOK & EXIT (CMP- 4230)")[0].event_type == "closed"


def _call(**overrides):
    base = {
        "posted_at": POSTED.isoformat(),
        "status": "open",
        "direction": "long",
        "entry_price": 90.0,
        "stop_loss": 80.0,
        "target_1": 100.0,
    }
    base.update(overrides)
    return base


def _candle(high, low, close):
    return {"ts": "2026-10-19T10:05:00+05:30", "high": high, "low": low, "close": close}


def test_price_tracking_hits_target():
    values = _evaluate_candles(_call(), [_candle(101, 89, 100.5)])
    assert values["status"] == "target_hit"
    assert values["status_source"] == "price"
    assert values["exit_price"] == 100.0
    assert values["realized_pct"] == pytest.approx(11.1111, abs=0.001)


def test_price_tracking_hits_stop_loss():
    values = _evaluate_candles(_call(), [_candle(95, 79, 81)])
    assert values["status"] == "sl_hit"
    assert values["exit_price"] == 80.0
    assert values["realized_pct"] == pytest.approx(-11.1111, abs=0.001)


def test_bar_touching_both_levels_is_ambiguous_rather_than_guessed():
    values = _evaluate_candles(_call(), [_candle(105, 75, 90)])
    assert values["tracking_status"] == "ambiguous"
    assert "status" not in values


def test_no_candles_after_the_call_is_reported_not_fabricated():
    values = _evaluate_candles(_call(), [{"ts": "2026-10-18T10:05:00+05:30", "high": 200, "low": 1, "close": 200}])
    assert values["tracking_status"] == "no_data"
    assert "status" not in values


def test_expired_contract_is_unknown_not_a_fabricated_pnl():
    values = _evaluate_candles(_call(expiry="2020-01-30"), [_candle(101, 89, 100.5)])
    assert values["status"] == "expired"
    assert values["status_reason"] == CONTRACT_EXPIRED_REASON
    assert values["tracking_status"] == "contract_expired"
    assert values["exit_price"] is None
    assert values["realized_pct"] is None
    assert values["r_multiple"] is None


def _seed_open_call(expiry: str, call_index: int) -> int:
    with get_conn() as conn:
        message = conn.execute(
            """
            INSERT INTO daily_byte_messages (group_slot, chat_id, message_id, posted_at, text)
            VALUES (1, 1, ?, '2026-01-01T00:00:00+00:00', 'BUY TEST')
            """,
            (call_index,),
        )
        call = conn.execute(
            """
            INSERT INTO telegram_calls
            (message_fk, chat_id, message_id, posted_at, call_index, symbol, segment,
             direction, action, expiry, method, extraction_json)
            VALUES (?, 1, ?, '2026-01-01T00:00:00+00:00', ?, 'TEST', 'stock_option',
                    'long', 'BUY', ?, 'regex', '{}')
            """,
            (message.lastrowid, call_index, call_index, expiry),
        )
        return int(call.lastrowid)


def test_bulk_expiry_closes_only_past_contracts_and_keeps_outcome_unknown():
    past_id = _seed_open_call("2020-01-30", 1)
    future_id = _seed_open_call("2999-01-01", 2)

    assert _expire_known_contracts() == 1

    with get_conn() as conn:
        past = dict(conn.execute("SELECT * FROM telegram_calls WHERE id = ?", (past_id,)).fetchone())
        future = dict(conn.execute("SELECT * FROM telegram_calls WHERE id = ?", (future_id,)).fetchone())

    assert past["status"] == "expired"
    assert past["status_reason"] == CONTRACT_EXPIRED_REASON
    assert past["tracking_status"] == "contract_expired"
    assert past["tracking_message"] == CONTRACT_EXPIRED_REASON
    assert past["exit_price"] is None
    assert past["realized_pct"] is None
    assert past["r_multiple"] is None
    assert past["closed_at"]

    assert future["status"] == "open"
    assert future["status_reason"] is None


def _seed_message(text: str, message_id: int, posted_at: str = "2026-10-19T04:20:49+00:00") -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO daily_byte_messages
            (group_slot, chat_id, chat_username, chat_title, message_id, posted_at, text, word_count)
            VALUES (1, 111, 'test_channel', 'Test Channel', ?, ?, ?, 10)
            """,
            (message_id, posted_at, text),
        )
        return dict(
            conn.execute("SELECT * FROM daily_byte_messages WHERE id = ?", (cur.lastrowid,)).fetchone()
        )


def test_reposted_contract_updates_the_open_call_instead_of_inserting_another():
    first = _seed_message("BUY NIFTY 23000 PE @ 149 SL 118 TGT 200", 1)
    second = _seed_message("BUY NIFTY 23000 PE @ 149 SL 120 TGT 210", 2)

    with get_conn() as conn:
        _store_extraction(conn, first, parse_message(first["text"], _parse_posted(first["posted_at"])), "regex", True)
        _store_extraction(conn, second, parse_message(second["text"], _parse_posted(second["posted_at"])), "regex", True)
        rows = [dict(r) for r in conn.execute("SELECT * FROM telegram_calls").fetchall()]

    assert len(rows) == 1
    [call] = rows
    assert call["message_id"] == 1
    assert call["entry_price"] == 149.0
    assert call["stop_loss"] == 120.0
    assert call["target_1"] == 210.0


def test_commodity_future_month_is_part_of_the_contract_identity():
    first = _seed_message("BUY MCX COPPER JUN AT 719.50 TP 723.50 SL 717", 21, "2026-06-01T04:00:00+00:00")
    second = _seed_message("BUY MCX COPPER JUL AT 734.10 TP 740.60 SL 728", 22, "2026-07-01T04:00:00+00:00")

    with get_conn() as conn:
        _store_extraction(conn, first, parse_message(first["text"], _parse_posted(first["posted_at"])), "regex", True)
        _store_extraction(conn, second, parse_message(second["text"], _parse_posted(second["posted_at"])), "regex", True)
        rows = [dict(r) for r in conn.execute("SELECT * FROM telegram_calls ORDER BY id").fetchall()]

    assert [(row["expiry"], row["entry_price"]) for row in rows] == [
        ("2026-06-30", 719.5),
        ("2026-07-31", 734.1),
    ]


def test_legacy_active_duplicates_collapse_without_deleting_archive_or_events():
    old = _seed_message("BUY MCX COPPER JUN AT 719.50 TP 723.50 SL 717", 11, "2026-06-01T04:00:00+00:00")
    new = _seed_message("BUY MCX COPPER JUL AT 734.10 TP 740.60 SL 728", 12, "2026-07-01T04:00:00+00:00")

    with get_conn() as conn:
        old_call = conn.execute(
            """
            INSERT INTO telegram_calls
            (message_fk, chat_id, chat_title, message_id, posted_at, symbol, segment,
             direction, action, entry_price, stop_loss, target_1, status, method, extraction_json)
            VALUES (?, 111, 'Test Channel', 11, ?, 'COPPER', 'commodity', 'long', 'BUY',
                    719.5, 717, 723.5, 'open', 'regex', '{}')
            """,
            (old["id"], old["posted_at"]),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO telegram_calls
            (message_fk, chat_id, chat_title, message_id, posted_at, symbol, segment,
             direction, action, entry_price, stop_loss, target_1, status, method, extraction_json)
            VALUES (?, 111, 'Test Channel', 12, ?, 'COPPER', 'commodity', 'long', 'BUY',
                    734.1, 728, 740.6, 'open', 'regex', '{}')
            """,
            (new["id"], new["posted_at"]),
        )
        conn.execute(
            """
            INSERT INTO telegram_call_events
            (call_id, message_fk, chat_id, message_id, posted_at, event_type, method, extracted_json)
            VALUES (?, ?, 111, 11, ?, 'update', 'regex', '{}')
            """,
            (old_call, old["id"], old["posted_at"]),
        )

    assert _collapse_duplicate_active_calls() == 1

    with get_conn() as conn:
        calls = [dict(r) for r in conn.execute("SELECT * FROM telegram_calls").fetchall()]
        archived = conn.execute("SELECT COUNT(*) FROM daily_byte_messages").fetchone()[0]
        event = dict(conn.execute("SELECT * FROM telegram_call_events").fetchone())

    assert len(calls) == 1
    assert calls[0]["message_id"] == 12
    assert archived == 2
    assert event["call_id"] is None


class _FakeMessage:
    def __init__(self, message_id: int):
        self.id = message_id
        self.date = datetime(2026, 10, 19, 13, 0, tzinfo=ZoneInfo("UTC"))
        self.message = f"alert {message_id}"


class _FakeTelegram:
    def __init__(self):
        self.calls = []
        self._messages = [_FakeMessage(i) for i in range(101, 106)]

    async def connect(self):
        return None

    async def is_user_authorized(self):
        return True

    async def get_entity(self, _ref):
        return object()

    async def get_messages(self, _entity, limit, offset_id, min_id):
        self.calls.append({"limit": limit, "offset_id": offset_id, "min_id": min_id})
        rows = [m for m in self._messages if m.id > min_id and (offset_id == 0 or m.id < offset_id)]
        return sorted(rows, key=lambda m: m.id, reverse=True)[:limit]


async def test_fetch_history_is_incremental_and_paginates_without_gaps():
    from app.integrations.telegram_client import TelegramDailyByteClient

    client = TelegramDailyByteClient()
    fake = _FakeTelegram()
    client._client = fake
    chat = {"ref": "test", "chat_id": 1, "username": "test", "title": "Test", "group_slot": 1}

    rows = await client.fetch_history(chat, limit=0, batch_size=2, min_id=100)

    assert [r["message_id"] for r in rows] == [105, 104, 103, 102, 101]
    assert [c["offset_id"] for c in fake.calls] == [0, 104, 102]
    assert {c["min_id"] for c in fake.calls} == {100}


async def test_session_connect_retries_transient_sqlite_lock(monkeypatch):
    import sqlite3

    from app.config import settings
    from app.integrations.telegram_client import TelegramDailyByteClient

    class _FlakyClient:
        def __init__(self):
            self.connect_attempts = 0

        async def connect(self):
            self.connect_attempts += 1
            if self.connect_attempts < 3:
                raise sqlite3.OperationalError("database is locked")

        async def is_user_authorized(self):
            return True

        async def disconnect(self):
            return None

    monkeypatch.setattr(settings, "telegram_api_id", 123)
    monkeypatch.setattr(settings, "telegram_api_hash", "hash")
    fake = _FlakyClient()
    client = TelegramDailyByteClient()
    client._build_client = lambda: fake

    await client.ensure_session()

    assert fake.connect_attempts == 3
