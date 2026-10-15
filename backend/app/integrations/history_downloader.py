"""Admin historical-data-download engine (proactive archival).

Spike findings that shape this module (see scripts/stock_history_probe.py):
- Definedge's history endpoint only serves data for a token that is still in
  the CURRENT instrument master. Three tokens that were live option
  contracts 9 days prior all returned 0 candles once expired — there is no
  symbol-search endpoint to resolve an expired contract to a token either.
  So this never attempts to backfill expired contracts; it only archives
  currently-listed equity/future/option instruments before they roll off.
- Equity 1-min history is capped at ~180 days back regardless of how far the
  request asks (365d and 730d requests returned identical data) — validate
  and download both clamp to `settings.history_equity_max_days_back`.
- A single call across the full available range works fine (confirmed up to
  730 days) — no day-by-day chunking is needed, simplifying the original
  plan's design.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from app.config import settings
from app.db import clickhouse
from app.db.sqlite import get_conn
from app.integrations import instruments
from app.integrations.definedge_client import DefinedgeAuthError
from app.models.schemas import (
    DownloadCoverageRow,
    DownloadJobItemStatus,
    DownloadJobStatus,
    DownloadScope,
    DownloadValidateItem,
    DownloadValidateResponse,
)

logger = logging.getLogger(__name__)

# In-process registry of running download jobs, for cancel support — fine for
# this single-process deployment (same pattern as core/security.py's
# in-process rate limiters).
_running_jobs: Dict[int, asyncio.Task] = {}
_cancel_flags: Dict[int, bool] = {}


class _RateLimiter:
    """Simple outbound throttle: sleeps as needed to keep calls to
    `settings.history_rps` requests/sec. Not thread-safe, fine for a single
    asyncio event loop."""

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


@dataclass
class _ResolvedInstrument:
    key: str  # e.g. "EQUITY|RELIANCE" or "OPTION|RELIANCE|2026-10-27|1170.0|CE"
    label: str
    kind: str  # "equity" | "future" | "option"
    exchange: str
    token: str
    expiry: Optional[date] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None


def _clamp_equity_start(start_date: date) -> date:
    floor = date.today() - timedelta(days=settings.history_equity_max_days_back)
    return max(start_date, floor)


async def _resolve_instruments(client, symbol: str, scope: DownloadScope) -> List[_ResolvedInstrument]:
    """Everything currently resolvable for this symbol within `scope` — the
    same resolution validate() and run_job() both use, so what validate
    reports is exactly what the job will attempt."""
    rows = await instruments.get_master_rows(client)
    today = date.today()
    out: List[_ResolvedInstrument] = []

    if scope.equity:
        try:
            eq = instruments.find_stock_equity(rows, symbol)
            out.append(_ResolvedInstrument(key=f"EQUITY|{symbol}", label=f"{symbol} equity", kind="equity", exchange=eq.exchange, token=eq.token))
        except instruments.ContractNotFound:
            pass

    if scope.futures:
        for expiry in instruments.list_future_expiries(rows, symbol, today):
            fut = instruments.find_stock_future(rows, symbol, expiry)
            out.append(
                _ResolvedInstrument(
                    key=f"FUTURE|{symbol}|{expiry.isoformat()}",
                    label=f"{symbol} FUT {expiry.isoformat()}",
                    kind="future",
                    exchange=fut.exchange,
                    token=fut.token,
                    expiry=expiry,
                )
            )

    if scope.options:
        option_expiries = instruments.list_option_expiries(rows, symbol, today)
        spot = None
        if option_expiries:
            try:
                eq = instruments.find_stock_equity(rows, symbol)
                quote = await client.get_quote(eq.exchange, eq.token)
                spot = float(quote["ltp"])
            except Exception as exc:  # noqa: BLE001 — fall back to strike-0 band if spot unavailable
                logger.warning("history_downloader: spot lookup failed for %s (%s)", symbol, type(exc).__name__)
        for expiry in option_expiries:
            for option_type in ("CE", "PE"):
                if spot is not None:
                    strikes = instruments.list_strikes_near(rows, symbol, expiry, option_type, spot, scope.strike_band)
                else:
                    all_strikes = sorted(
                        {
                            float(r["strike"])
                            for r in rows
                            if r["segment"] == "NFO" and r["symbol"] == symbol and r["instrument_type"] == "OPTSTK"
                            and r["option_type"] == option_type and r["expiry"] == expiry.strftime("%d%m%Y")
                        }
                    )
                    strikes = all_strikes[: scope.strike_band]
                for strike in strikes:
                    try:
                        opt = instruments.find_stock_option(rows, symbol, expiry, strike, option_type)
                    except instruments.ContractNotFound:
                        continue
                    out.append(
                        _ResolvedInstrument(
                            key=f"OPTION|{symbol}|{expiry.isoformat()}|{strike}|{option_type}",
                            label=f"{symbol} {expiry.isoformat()} {strike}{option_type}",
                            kind="option",
                            exchange=opt.exchange,
                            token=opt.token,
                            expiry=expiry,
                            strike=strike,
                            option_type=option_type,
                        )
                    )
    return out


async def validate(symbol: str, start_date: date, end_date: date, scope: DownloadScope) -> DownloadValidateResponse:
    from app.api.routes.auth import _client, ensure_authenticated

    warnings: List[str] = []
    clamped_start = _clamp_equity_start(start_date) if scope.equity else start_date
    if clamped_start != start_date:
        warnings.append(f"Equity history is only available back to {clamped_start.isoformat()} (~{settings.history_equity_max_days_back}d) — start date clamped.")
    clamped_end = min(end_date, date.today())
    if clamped_end != end_date:
        warnings.append("End date clamped to today.")
    if clamped_start > clamped_end:
        warnings.append("Clamped start date is after end date — nothing to download.")

    try:
        await ensure_authenticated()
        resolved = await _resolve_instruments(_client, symbol, scope)
    except DefinedgeAuthError:
        return DownloadValidateResponse(
            symbol=symbol, clamped_start_date=clamped_start, clamped_end_date=clamped_end,
            items=[], estimated_candle_calls=0, warnings=["Broker session unavailable — cannot validate."],
        )

    if not resolved:
        warnings.append(f"No currently-listed F&O contracts found for {symbol} with this scope.")

    items = [DownloadValidateItem(instrument_key=r.key, label=r.label, available=True) for r in resolved]
    return DownloadValidateResponse(
        symbol=symbol,
        clamped_start_date=clamped_start,
        clamped_end_date=clamped_end,
        items=items,
        estimated_candle_calls=len(resolved),
        warnings=warnings,
    )


def _option_candle_rows(symbol: str, expiry: date, strike: float, option_type: str, candles: list) -> list:
    return [
        (
            symbol, expiry, strike, option_type,
            datetime.fromisoformat(c["ts"]),
            c["open"], c["high"], c["low"], c["close"],
            int(c["volume"]), int(c["oi"] or 0), 0, "admin_download",
        )
        for c in candles
    ]


def _underlying_candle_rows(symbol: str, instrument_type: str, expiry: Optional[date], candles: list) -> list:
    return [
        (
            symbol, instrument_type, expiry or date(1970, 1, 1),
            datetime.fromisoformat(c["ts"]),
            c["open"], c["high"], c["low"], c["close"],
            int(c["volume"]), int(c["oi"] or 0), "admin_download",
        )
        for c in candles
    ]


def _update_job(job_id: int, **fields) -> None:
    with get_conn() as conn:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE download_jobs SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            (*fields.values(), job_id),
        )


def _record_item(job_id: int, key: str, status: str, rows: int, error: Optional[str] = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO download_job_items (job_id, instrument_key, status, rows, error) VALUES (?, ?, ?, ?, ?)",
            (job_id, key, status, rows, error),
        )
        conn.execute(
            "UPDATE download_jobs SET completed_items = completed_items + 1, total_rows = total_rows + ?, updated_at = datetime('now') WHERE id = ?",
            (rows, job_id),
        )


async def _fetch_with_retry(client, exchange: str, token: str, start: datetime, end: datetime) -> list:
    last_exc: Optional[Exception] = None
    for attempt in range(settings.history_max_retries):
        try:
            return await client.get_historical_candles(exchange, token, "minute", start, end)
        except Exception as exc:  # noqa: BLE001 — retried; final failure re-raised below
            last_exc = exc
            await asyncio.sleep(min(2**attempt, 10))
    raise last_exc  # type: ignore[misc]


async def _run_job_body(job_id: int, symbol: str, start_date: date, end_date: date, scope: DownloadScope) -> None:
    from app.api.routes.auth import _client, ensure_authenticated

    limiter = _RateLimiter(settings.history_rps)
    try:
        await ensure_authenticated()
        resolved = await _resolve_instruments(_client, symbol, scope)
    except DefinedgeAuthError:
        _update_job(job_id, status="failed", error="Broker session unavailable")
        return
    except Exception as exc:  # noqa: BLE001
        _update_job(job_id, status="failed", error=f"Resolution failed: {type(exc).__name__}")
        return

    _update_job(job_id, status="running", total_items=len(resolved))
    clamped_start = _clamp_equity_start(start_date)
    clickhouse_client = clickhouse.get_client()

    for item in resolved:
        if _cancel_flags.get(job_id):
            _update_job(job_id, status="cancelled")
            return
        await limiter.wait()
        start_dt = datetime.combine(clamped_start if item.kind == "equity" else start_date, datetime.min.time())
        end_dt = datetime.combine(min(end_date, date.today()), datetime.min.time().replace(hour=23, minute=59))
        try:
            candles = await _fetch_with_retry(_client, item.exchange, item.token, start_dt, end_dt)
            if item.kind == "option":
                rows = _option_candle_rows(symbol, item.expiry, item.strike, item.option_type, candles)
                n = clickhouse.insert_option_candles(clickhouse_client, rows)
            else:
                rows = _underlying_candle_rows(symbol, "EQUITY" if item.kind == "equity" else "FUTURE", item.expiry, candles)
                n = clickhouse.insert_underlying_candles(clickhouse_client, rows)
            _record_item(job_id, item.key, "done", n)
        except Exception as exc:  # noqa: BLE001 — one failing contract shouldn't abort the whole job
            logger.warning("history_downloader: %s failed (%s)", item.key, type(exc).__name__)
            _record_item(job_id, item.key, "failed", 0, error=type(exc).__name__)

    with get_conn() as conn:
        row = conn.execute("SELECT status FROM download_jobs WHERE id = ?", (job_id,)).fetchone()
    if row and row["status"] != "cancelled":
        _update_job(job_id, status="completed")


def start_job(job_id: int, symbol: str, start_date: date, end_date: date, scope: DownloadScope) -> None:
    _cancel_flags[job_id] = False
    task = asyncio.create_task(_run_job_body(job_id, symbol, start_date, end_date, scope))
    _running_jobs[job_id] = task


def cancel_job(job_id: int) -> bool:
    if job_id not in _running_jobs:
        return False
    _cancel_flags[job_id] = True
    return True


def list_job_items(job_id: int) -> List[DownloadJobItemStatus]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM download_job_items WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
    return [DownloadJobItemStatus(instrument_key=r["instrument_key"], status=r["status"], rows=r["rows"], error=r["error"]) for r in rows]


def get_job(job_id: int) -> Optional[DownloadJobStatus]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM download_jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    return DownloadJobStatus(
        id=row["id"], symbol=row["symbol"], start_date=row["start_date"], end_date=row["end_date"],
        status=row["status"], total_items=row["total_items"], completed_items=row["completed_items"],
        total_rows=row["total_rows"], error=row["error"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def list_jobs() -> List[DownloadJobStatus]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM download_jobs ORDER BY id DESC LIMIT 50").fetchall()
    return [
        DownloadJobStatus(
            id=r["id"], symbol=r["symbol"], start_date=r["start_date"], end_date=r["end_date"],
            status=r["status"], total_items=r["total_items"], completed_items=r["completed_items"],
            total_rows=r["total_rows"], error=r["error"], created_at=r["created_at"], updated_at=r["updated_at"],
        )
        for r in rows
    ]


def create_job_row(symbol: str, start_date: date, end_date: date, scope: DownloadScope) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO download_jobs (symbol, start_date, end_date, scope_json) VALUES (?, ?, ?, ?)",
            (symbol, start_date.isoformat(), end_date.isoformat(), json.dumps(scope.model_dump())),
        )
        return cur.lastrowid


def coverage(symbol: str) -> List[DownloadCoverageRow]:
    client = clickhouse.get_client()
    out = []
    opt = client.query(
        "SELECT count() AS c, min(ts) AS lo, max(ts) AS hi FROM options_1min_candles WHERE symbol = {s:String}",
        parameters={"s": symbol},
    ).result_rows
    if opt and opt[0][0]:
        out.append(DownloadCoverageRow(instrument_type="OPTION", count=opt[0][0], min_ts=opt[0][1], max_ts=opt[0][2]))
    for itype in ("EQUITY", "FUTURE"):
        res = client.query(
            "SELECT count() AS c, min(ts) AS lo, max(ts) AS hi FROM underlying_1min_candles WHERE symbol = {s:String} AND instrument_type = {t:String}",
            parameters={"s": symbol, "t": itype},
        ).result_rows
        if res and res[0][0]:
            out.append(DownloadCoverageRow(instrument_type=itype, count=res[0][0], min_ts=res[0][1], max_ts=res[0][2]))
    return out
