"""Market-breadth worker.

Periodically (breadth_interval_minutes) resolves the configured universe (all
F&O stocks, the 50-stock watchlist, or an explicit list), fetches each
symbol's daily candles from Definedge, and persists daily / weekly / monthly
breadth series (advance-decline + %-above-DMA) into `breadth_snapshots`.
Intraday hourly breadth is opt-in (breadth_intraday_enabled) because it costs
a 1-min candle fetch per symbol.

Degraded behaviour mirrors str_stra_loop: if Definedge is unavailable or no
history comes back, it records status and writes nothing — never fabricates
breadth.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from app.config import settings
from app.db.sqlite import get_conn
from app.screener.breadth import daily_breadth_series, hourly_breadth_series, period_breadth_series

logger = logging.getLogger(__name__)


class _RateLimiter:
    """Minimal outbound throttle to `settings.history_rps` requests/sec
    (kept local to avoid pulling the ClickHouse-backed history_downloader into
    this worker's import chain)."""

    def __init__(self, rps: float):
        self._min_interval = 1.0 / rps if rps > 0 else 0.0
        self._last_call = 0.0

    async def wait(self) -> None:
        if self._min_interval <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_call
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()

_status: dict = {"state": "starting", "message": "breadth has not run yet", "last_run": None}


def get_status() -> dict:
    return dict(_status)


def _set_status(state: str, message: str) -> None:
    _status.update(state=state, message=message, last_run=datetime.now(timezone.utc).isoformat())


def _dma_periods() -> List[int]:
    return [int(p) for p in settings.breadth_dma_periods.split(",") if p.strip().isdigit()]


def _universe_symbols(rows: List[dict]) -> List[str]:
    if settings.breadth_universe == "watchlist":
        from app.screener.str_stra import WATCHLIST

        return [sym for sym, _, _ in WATCHLIST]
    if settings.breadth_universe == "fno":
        from app.integrations.instruments import list_fno_stocks

        return [s["symbol"] for s in list_fno_stocks(rows)]
    return [s.strip().upper() for s in settings.breadth_universe.split(",") if s.strip()]


def _upsert_snapshot(conn, bucket: str, ts: str, metrics: dict) -> None:
    conn.execute(
        """
        INSERT INTO breadth_snapshots (bucket, ts, metrics_json, computed_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(bucket, ts) DO UPDATE SET metrics_json = excluded.metrics_json, computed_at = datetime('now')
        """,
        (bucket, ts, json.dumps(metrics)),
    )


async def _fetch_daily(client, rows: List[dict], symbols: List[str], start_dt: datetime, end_dt: datetime) -> Dict[str, List[Tuple]]:
    from app.integrations.instruments import find_stock_equity

    limiter = _RateLimiter(settings.history_rps)
    sem = asyncio.Semaphore(6)

    async def one(sym: str):
        async with sem:
            await limiter.wait()
            try:
                eq = find_stock_equity(rows, sym)
                candles = await client.get_historical_candles(eq.exchange, eq.token, "day", start_dt, end_dt)
                closes = [(datetime.fromisoformat(c["ts"]).date(), c["close"]) for c in candles]
                return sym, closes
            except Exception as exc:  # noqa: BLE001 — per-symbol failure is non-fatal
                logger.warning("breadth: daily history failed for %s (%s)", sym, type(exc).__name__)
                return sym, None

    fetched = await asyncio.gather(*(one(sym) for sym in symbols))
    return {sym: closes for sym, closes in fetched if closes}


async def _fetch_intraday(client, rows: List[dict], symbols: List[str]) -> Dict[str, List[Tuple]]:
    from app.integrations.instruments import find_stock_equity

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=1)
    limiter = _RateLimiter(settings.history_rps)
    sem = asyncio.Semaphore(6)

    async def one(sym: str):
        async with sem:
            await limiter.wait()
            try:
                eq = find_stock_equity(rows, sym)
                candles = await client.get_historical_candles(eq.exchange, eq.token, "minute", start_dt, end_dt)
                points = [(datetime.fromisoformat(c["ts"]), c["close"]) for c in candles]
                return sym, points
            except Exception as exc:  # noqa: BLE001
                logger.warning("breadth: intraday history failed for %s (%s)", sym, type(exc).__name__)
                return sym, None

    fetched = await asyncio.gather(*(one(sym) for sym in symbols))
    return {sym: points for sym, points in fetched if points}


async def run_breadth_job() -> None:
    if not settings.breadth_enabled:
        return

    from app.api.routes.auth import _client, ensure_authenticated
    from app.integrations import instruments
    from app.integrations.definedge_client import DefinedgeAuthError

    def degraded(message: str) -> None:
        _set_status("degraded", message)
        logger.warning("breadth degraded: %s", message)

    try:
        await ensure_authenticated()
        rows = await instruments.get_master_rows(_client)
    except DefinedgeAuthError as exc:
        degraded(f"Definedge not authenticated ({exc})")
        return
    except Exception as exc:  # noqa: BLE001 — master download failure
        degraded(f"instrument master unavailable ({type(exc).__name__})")
        return

    symbols = _universe_symbols(rows)
    if not symbols:
        degraded("breadth universe resolved to zero symbols")
        return

    dma = _dma_periods()
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=settings.breadth_daily_history_days)

    per_symbol = await _fetch_daily(_client, rows, symbols, start_dt, end_dt)
    if not per_symbol:
        degraded("no daily history returned for any universe symbol")
        return

    daily = daily_breadth_series(per_symbol, dma)
    weekly = period_breadth_series(per_symbol, dma, "week")
    monthly = period_breadth_series(per_symbol, dma, "month")

    hourly = []
    if settings.breadth_intraday_enabled:
        intraday = await _fetch_intraday(_client, rows, symbols)
        if intraday:
            hourly = hourly_breadth_series(intraday)

    with get_conn() as conn:
        for s in daily:
            _upsert_snapshot(conn, "day", s["ts"], s)
        for s in weekly:
            _upsert_snapshot(conn, "week", s["ts"], s)
        for s in monthly:
            _upsert_snapshot(conn, "month", s["ts"], s)
        for s in hourly:
            _upsert_snapshot(conn, "hour", s["ts"], s)

    _set_status("ok", f"breadth over {len(symbols)} symbols ({len(daily)} daily buckets)")
    logger.info("breadth: %d symbols, %d daily / %d weekly / %d monthly / %d hourly buckets",
                len(symbols), len(daily), len(weekly), len(monthly), len(hourly))
