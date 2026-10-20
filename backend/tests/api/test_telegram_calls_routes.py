"""Trade-call tracker read APIs and the auditable manual-close path."""

import json

import pytest

from app.db.sqlite import get_conn

TEXT = "SAHI Trade Alert : 19 Oct\nBUY NIFTY 23000 PE @ 149 SL 118 TGT 200"


def _seed() -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO daily_byte_messages
            (group_slot, chat_id, chat_username, chat_title, message_id, posted_at, text, word_count)
            VALUES (1, 111, 'sahi_research', 'Sahi Research', 10, '2026-10-19T04:20:49+00:00', ?, 12)
            """,
            (TEXT,),
        )
        message_fk = cur.lastrowid
        conn.execute(
            """
            INSERT INTO telegram_message_classifications
            (message_fk, category, confidence, summary, is_trade_call, method, extracted_json)
            VALUES (?, 'trade_alert', 0.9, ?, 1, 'regex', ?)
            """,
            (message_fk, "BUY NIFTY 23000 PE", json.dumps({"category": "trade_alert"})),
        )
        cur = conn.execute(
            """
            INSERT INTO telegram_calls
            (message_fk, chat_id, chat_username, chat_title, message_id, posted_at,
             symbol, display_name, segment, direction, action, option_type, strike, expiry,
             entry_price, stop_loss, target_1, status, method, extraction_json)
            VALUES (?, 111, 'sahi_research', 'Sahi Research', 10, '2026-10-19T04:20:49+00:00',
                    'NIFTY', 'NIFTY', 'index_option', 'long', 'BUY', 'PE', 23000, '2026-10-27',
                    149, 118, 200, 'open', 'regex', ?)
            """,
            (message_fk, json.dumps({"symbol": "NIFTY", "entry_price": 149})),
        )
        return cur.lastrowid


def test_calls_stats_archive_and_detail(client, auth):
    call_id = _seed()

    calls = client.get("/api/v1/telegram/calls", headers=auth)
    assert calls.status_code == 200
    body = calls.json()
    assert body["total"] == 1
    assert body["items"][0]["symbol"] == "NIFTY"
    assert body["items"][0]["source_url"].endswith("/10")

    stats = client.get("/api/v1/telegram/stats", headers=auth).json()
    assert stats["total_calls"] == 1
    assert stats["open"] == 1
    assert stats["win_rate"] is None

    archive = client.get("/api/v1/telegram/messages", headers=auth).json()
    assert archive["total"] == 1
    assert archive["items"][0]["category"] == "trade_alert"
    assert archive["items"][0]["call_count"] == 1

    chats = client.get("/api/v1/telegram/chats", headers=auth).json()
    assert chats[0]["chat_id"] == 111
    assert chats[0]["open_count"] == 1

    detail = client.get(f"/api/v1/telegram/calls/{call_id}", headers=auth).json()
    assert detail["status"] == "open"
    assert detail["extraction"]["entry_price"] == 149
    assert len(detail["thread"]) == 1


def test_manual_close_is_labelled_manual_and_computes_pnl(client, auth):
    call_id = _seed()
    resp = client.post(
        f"/api/v1/telegram/calls/{call_id}/close",
        headers=auth,
        json={"exit_price": 180, "notes": "closed at market"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "closed"
    assert body["status_source"] == "manual"
    assert body["exit_price"] == 180
    assert body["realized_pct"] == pytest.approx(20.8054, abs=0.01)
    assert body["r_multiple"] == pytest.approx(1.0, abs=0.01)


def test_missing_call_returns_404(client, auth):
    assert client.get("/api/v1/telegram/calls/999", headers=auth).status_code == 404
    assert client.post("/api/v1/telegram/calls/999/close", headers=auth, json={"exit_price": 1}).status_code == 404



def test_call_chart_resolves_contract_and_returns_five_minute_candles(client, auth, monkeypatch):
    call_id = _seed()
    from app.api.routes import telegram_calls as route

    master_row = {
        "segment": "NFO",
        "instrument_type": "OPTIDX",
        "symbol": "NIFTY",
        "option_type": "PE",
        "expiry": "27102026",
        "strike": "23000",
        "token": "12345",
        "trading_symbol": "NIFTY27OCT2623000PE",
        "lot_size": "50",
        "tick_size": "5",
    }

    async def fake_auth():
        return None

    async def fake_master(_client):
        return [master_row]

    async def fake_history(exchange, token, timeframe, start, end):
        assert exchange == "NFO"
        assert token == "12345"
        assert timeframe == "minute"
        return [
            {
                "ts": f"2026-10-19T09:{minute:02d}:00",
                "open": 150 + minute,
                "high": 151 + minute,
                "low": 149 + minute,
                "close": 150.5 + minute,
                "volume": 10,
                "oi": 100,
            }
            for minute in range(15, 25)
        ]

    monkeypatch.setattr(route, "ensure_authenticated", fake_auth)
    monkeypatch.setattr(route, "get_master_rows", fake_master)
    monkeypatch.setattr(route._client, "get_historical_candles", fake_history)

    resp = client.get(f"/api/v1/telegram/calls/{call_id}/chart?interval=5", headers=auth)
    assert resp.status_code == 200
    body = resp.json()
    assert body["trading_symbol"] == "NIFTY27OCT2623000PE"
    assert body["interval_minutes"] == 5
    assert len(body["candles"]) == 2
    assert body["levels"]["entry"] == 149
    assert body["levels"]["stop_loss"] == 118
    assert body["levels"]["targets"] == [200]
    assert body["ltp"] == body["candles"][-1]["close"]
    # Instrument details the chart modal explains, and the exact call time the
    # frontend anchors its vertical marker to.
    assert body["segment"] == "index_option"
    assert body["instrument_type"] == "OPTIDX"
    assert body["underlying"] == "NIFTY"
    assert body["strike"] == 23000
    assert body["expiry"] == "2026-10-27"
    assert body["option_type"] == "PE"
    assert body["lot_size"] == 50
    assert body["tick_size"] == 0.05
    assert body["posted_at_utc_seconds"] == 1792383649


def test_call_chart_404_for_unknown_call(client, auth):
    assert client.get("/api/v1/telegram/calls/999/chart?interval=5", headers=auth).status_code == 404
