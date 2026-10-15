"""Tests for the chart-history write-through cache (chart_history.py)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.integrations import chart_history
from app.integrations.definedge_client import Candle

IST = ZoneInfo("Asia/Kolkata")


def _candle(ts: str, close: float) -> Candle:
    return Candle(ts=ts, open=close, high=close, low=close, close=close, volume=0, oi=None)


def _recent_ts(minutes_ago: int = 0) -> str:
    return (datetime.now(IST).replace(tzinfo=None) - timedelta(minutes=minutes_ago)).isoformat()


class _Dg:
    def __init__(self, candles):
        self.candles = candles
        self.fetched = False

    async def get_historical_candles(self, exchange, token, timeframe, start, end):
        self.fetched = True
        return self.candles


# --- pure helpers ---


def test_dedup_keeps_last_and_orders():
    a = _candle("2024-01-01T09:15:00", 100)
    b = _candle("2024-01-01T09:15:00", 101)  # duplicate ts, later in list → wins
    c = _candle("2024-01-01T09:14:00", 99)
    out = chart_history._dedup([a, c, b])
    assert [x["ts"] for x in out] == ["2024-01-01T09:14:00", "2024-01-01T09:15:00"]
    assert out[-1]["close"] == 101


def test_row_to_candle():
    row = (datetime(2024, 1, 1, 9, 15), 100.0, 105.0, 99.0, 104.0, 1234, 567)
    c = chart_history._row_to_candle(row)
    assert c["ts"] == "2024-01-01T09:15:00"
    assert (c["open"], c["high"], c["low"], c["close"]) == (100.0, 105.0, 99.0, 104.0)
    assert c["volume"] == 1234 and c["oi"] == 567


# --- write rows carry the `backfill` source (no Enum8 migration) ---


def test_option_rows_shape_and_source():
    rows = chart_history._option_rows("NIFTY", None, 21800.0, "PE", [_candle("2024-01-01T09:15:00", 100)])
    assert len(rows) == 1
    row = rows[0]
    assert row[3] == "PE"
    assert row[4] == datetime(2024, 1, 1, 9, 15)
    assert row[-1] == "backfill"
    assert len(row) == 13  # OPTION_CANDLE_COLUMNS


def test_future_rows_shape_and_source():
    rows = chart_history._future_rows("NIFTY", None, [_candle("2024-01-01T09:15:00", 100)])
    assert len(rows) == 1
    row = rows[0]
    assert row[1] == "FUTURE"
    assert row[-1] == "backfill"
    assert len(row) == 11  # UNDERLYING_CANDLE_COLUMNS


# --- write-through behavior ---


async def test_ensure_chart_history_cache_hit(monkeypatch):
    recent = [_candle(_recent_ts(0), 100)]
    monkeypatch.setattr(chart_history, "read_option_candles", lambda *a, **k: recent)
    monkeypatch.setattr(chart_history.clickhouse, "get_client", lambda: object())
    dg = _Dg([])

    out = await chart_history.ensure_chart_history(
        dg, exchange="NFO", token="1", symbol="NIFTY", kind="option",
        expiry=None, strike=21800.0, option_type="PE",
    )
    assert out == recent
    assert dg.fetched is False


async def test_ensure_chart_history_cache_miss_fetches_and_stores(monkeypatch):
    monkeypatch.setattr(chart_history, "read_option_candles", lambda *a, **k: [])
    stored = []
    monkeypatch.setattr(
        chart_history, "store_option_candles",
        lambda ch, s, e, k, ot, candles: stored.append(len(candles)) or len(candles),
    )
    monkeypatch.setattr(chart_history.clickhouse, "get_client", lambda: object())
    fetched = [_candle(_recent_ts(0), 100), _candle(_recent_ts(-1), 101)]
    dg = _Dg(fetched)

    out = await chart_history.ensure_chart_history(
        dg, exchange="NFO", token="1", symbol="NIFTY", kind="option",
        expiry=None, strike=21800.0, option_type="PE",
    )
    assert dg.fetched is True
    assert out == fetched
    assert stored == [2]


async def test_ensure_chart_history_stale_cache_is_a_miss(monkeypatch):
    # Cached candles older than the 45-day lookback are treated as a miss.
    stale = [_candle("2020-01-01T09:15:00", 100)]
    monkeypatch.setattr(chart_history, "read_future_candles", lambda *a, **k: stale)
    stored = []
    monkeypatch.setattr(
        chart_history, "store_future_candles",
        lambda ch, s, e, candles: stored.append(len(candles)) or len(candles),
    )
    monkeypatch.setattr(chart_history.clickhouse, "get_client", lambda: object())
    fetched = [_candle(_recent_ts(0), 100)]
    dg = _Dg(fetched)

    out = await chart_history.ensure_chart_history(
        dg, exchange="NFO", token="9", symbol="NIFTY", kind="future", expiry=None,
    )
    assert dg.fetched is True
    assert out == fetched
    assert stored == [1]


async def test_ensure_chart_history_rejects_bad_kind():
    with pytest.raises(ValueError):
        await chart_history.ensure_chart_history(
            _Dg([]), exchange="NFO", token="1", symbol="NIFTY", kind="equity", expiry=None,
        )
