"""Chart-history write-through cache for the P&F / Renko transforms.

The transforms endpoint needs ~30 trading sessions of 1-min candles per
contract. Rather than refetching from Definedge on every request, read from
ClickHouse first (populated by the admin downloader and/or earlier requests);
on a cache miss, fetch from Definedge and write through so the next request
is served from ClickHouse. Reuses the admin downloader's insert helpers.

`source` is tagged `backfill` (an existing Enum8 value) — no ClickHouse
migration is needed (FEATURE_CHARTS_PLAN.md §5.4).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List
from zoneinfo import ZoneInfo

from app.db import clickhouse
from app.integrations.definedge_client import Candle, DefinedgeClient

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ~30 trading sessions of lookback. 45 calendar days covers weekends/holidays
# generously (the admin history probe showed one call handles ≥730 days).
HISTORY_DAYS_BACK = 45


def _now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


def _lookback_start() -> datetime:
    return _now_ist() - timedelta(days=HISTORY_DAYS_BACK)


def _row_to_candle(row) -> Candle:
    ts, open_, high, low, close, volume, oi = row
    if isinstance(ts, datetime):
        ts_str = ts.replace(tzinfo=None).isoformat()
    else:
        ts_str = str(ts)
    return Candle(
        ts=ts_str,
        open=float(open_),
        high=float(high),
        low=float(low),
        close=float(close),
        volume=int(volume),
        oi=int(oi) if oi is not None else None,
    )


def _dedup(candles: List[Candle]) -> List[Candle]:
    """ReplacingMergeTree may not have merged duplicates yet — keep the last
    row per timestamp, preserving chronological order."""
    by_ts = {c["ts"]: c for c in candles}
    return sorted(by_ts.values(), key=lambda c: c["ts"])


def read_option_candles(ch_client, symbol: str, expiry, strike: float, option_type: str) -> List[Candle]:
    rows = ch_client.query(
        "SELECT ts, open, high, low, close, volume, oi FROM options_1min_candles "
        "WHERE symbol = {s:String} AND expiry = {e:Date} AND strike = {k:Decimal(10,2)} "
        "AND option_type = {ot:String} ORDER BY ts",
        parameters={"s": symbol, "e": expiry, "k": strike, "ot": option_type},
    ).result_rows
    return _dedup([_row_to_candle(r) for r in rows])


def read_future_candles(ch_client, symbol: str, expiry) -> List[Candle]:
    rows = ch_client.query(
        "SELECT ts, open, high, low, close, volume, oi FROM underlying_1min_candles "
        "WHERE symbol = {s:String} AND instrument_type = {t:String} AND expiry = {e:Date} ORDER BY ts",
        parameters={"s": symbol, "t": "FUTURE", "e": expiry},
    ).result_rows
    return _dedup([_row_to_candle(r) for r in rows])


def _option_rows(symbol: str, expiry, strike: float, option_type: str, candles: List[Candle]) -> list:
    return [
        (
            symbol, expiry, strike, option_type, datetime.fromisoformat(c["ts"]),
            c["open"], c["high"], c["low"], c["close"],
            int(c["volume"]), int(c["oi"] or 0), 0, "backfill",
        )
        for c in candles
    ]


def _future_rows(symbol: str, expiry, candles: List[Candle]) -> list:
    return [
        (
            symbol, "FUTURE", expiry, datetime.fromisoformat(c["ts"]),
            c["open"], c["high"], c["low"], c["close"],
            int(c["volume"]), int(c["oi"] or 0), "backfill",
        )
        for c in candles
    ]


def store_option_candles(ch_client, symbol: str, expiry, strike: float, option_type: str, candles: List[Candle]) -> int:
    return clickhouse.insert_option_candles(ch_client, _option_rows(symbol, expiry, strike, option_type, candles))


def store_future_candles(ch_client, symbol: str, expiry, candles: List[Candle]) -> int:
    return clickhouse.insert_underlying_candles(ch_client, _future_rows(symbol, expiry, candles))


async def ensure_chart_history(
    dg_client: DefinedgeClient,
    *,
    exchange: str,
    token: str,
    symbol: str,
    kind: str,  # "future" | "option"
    expiry,
    strike: float | None = None,
    option_type: str | None = None,
) -> List[Candle]:
    """Return the last ~30 sessions of 1-min candles for a contract.

    Reads ClickHouse first; if the recent window is empty (cache miss),
    fetches from Definedge and writes through so the next request is served
    from ClickHouse. `kind` selects the target table.
    """
    ch_client = clickhouse.get_client()
    start = _lookback_start()

    if kind == "option":
        candles = read_option_candles(ch_client, symbol, expiry, strike, option_type)
    elif kind == "future":
        candles = read_future_candles(ch_client, symbol, expiry)
    else:
        raise ValueError(f"kind must be 'future' or 'option', got {kind!r}")

    recent = [c for c in candles if datetime.fromisoformat(c["ts"]) >= start]
    if recent:
        return recent

    logger.info("chart_history: cache miss for %s %s — fetching from Definedge", symbol, token)
    fetched = await dg_client.get_historical_candles(exchange, token, "minute", start, _now_ist())
    if kind == "option":
        store_option_candles(ch_client, symbol, expiry, strike, option_type, fetched)
    else:
        store_future_candles(ch_client, symbol, expiry, fetched)
    return fetched
