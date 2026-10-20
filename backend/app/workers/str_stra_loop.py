"""
Periodic str-stra screener run — the port of the `str-stra/` scripts into the
FastAPI backend. Screens the 50-stock watchlist with the three-pass pipeline
(ADX → candle "inaction" → straddle parity), tiers the results (PRIME/READY/
WATCH), and persists one accepted signal per qualifying (strategy_type, symbol)
pair into `screener_signals`.

Data comes from the same Definedge Integrate connection the credit-spread
screener already uses (daily equity candles via the instrument master +
`/history`, and live NFO futures/options quotes via `/quotes`). Every row is
tagged `data_source="live"`. When Definedge is unreachable or unauthenticated,
the run emits NO signals and records a degraded run — it never fabricates
prices.
"""

import asyncio
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from app.config import settings
from app.db.sqlite import get_conn
from app.screener.str_stra import (
    ADX_PERIOD,
    ADX_TREND_WINDOW,
    AVG_PERIOD,
    WATCHLIST,
    snap,
    straddle_metrics,
    technical_metrics,
    tier_of,
)

logger = logging.getLogger(__name__)

_status: dict = {"state": "starting", "message": "str-stra screener has not run yet", "last_run": None}


def get_status() -> dict:
    return dict(_status)


def _set_status(state: str, message: str) -> None:
    _status.update(state=state, message=message, last_run=datetime.now(timezone.utc).isoformat())


def _marks(pass_: bool) -> str:
    return "✓" if pass_ else "✗"


def _reason(strategy_type: str, m: dict) -> str:
    sym = m["symbol"]
    if strategy_type == "straddle":
        return (
            f"{sym} ATM {m['atm']} in parity — CE {m['ce_ltp']} vs PE {m['pe_ltp']} "
            f"(spread {m['spread_pct']}%). Short-straddle premium ₹{m['straddle_prem']:.2f}."
        )
    if strategy_type == "strangle":
        return (
            f"{sym} OTM strangle — sell CE/PE one step from ATM. Premium ₹{m['strangle_prem']:.2f} "
            f"(lower-margin alternative to the ATM straddle)."
        )
    if strategy_type == "adx_direction":
        return (
            f"{sym} ADX {m['adx']} ({m['adx_trend']}, {m['adx_chg_pct']:+.1f}%) with a supply candle "
            f"on {m['volume_pct']:+.1f}% vs 20-day volume — 'inaction' setup."
        )
    if strategy_type == "unified":
        return (
            f"{sym} {m['tier']} — {m['n_pass']}/3 passes "
            f"(ADX {_marks(m['adx_pass'])}, Candle {_marks(m['candle_pass'])}, Parity {_marks(m['straddle_pass'])})."
        )
    return f"{sym} {strategy_type} signal."


def _decision(m: dict, parity_pct: float, history_days: int) -> dict:
    computed = {
        "adx": m.get("adx"),
        "adx_change_pct": m.get("adx_chg_pct"),
        "adx_trend": m.get("adx_trend"),
        "plus_di": m.get("plus_di"),
        "minus_di": m.get("minus_di"),
        "dmi_trend": m.get("dmi_trend"),
        "candle_type": m.get("candle_type"),
        "volume_pct": m.get("volume_pct"),
        "comment": m.get("comment"),
        "pattern": m.get("pattern"),
        "fut_ltp": m.get("fut_ltp"),
        "atm": m.get("atm"),
        "ce_ltp": m.get("ce_ltp"),
        "pe_ltp": m.get("pe_ltp"),
        "spread_pct": m.get("spread_pct"),
        "straddle_prem": m.get("straddle_prem"),
        "straddle_val": m.get("straddle_val"),
        "strangle_prem": m.get("strangle_prem"),
        "strangle_val": m.get("strangle_val"),
        "tier": m.get("tier"),
        "n_pass": m.get("n_pass"),
        "adx_pass": m.get("adx_pass"),
        "candle_pass": m.get("candle_pass"),
        "straddle_pass": m.get("straddle_pass"),
        "lot": m.get("lot"),
    }
    params = {
        "adx_period": ADX_PERIOD,
        "adx_trend_window": ADX_TREND_WINDOW,
        "avg_period": AVG_PERIOD,
        "atm_parity_pct": parity_pct,
        "history_days": history_days,
    }
    return {"params": params, "computed": computed, "reject_reason": None}


def _legs_for(strategy_type: str, m: dict) -> List[dict]:
    """Option legs for a str-stra signal, so the signal-detail view can chart
    the underlying future + the combined premium (straddle/strangle) or just
    the underlying (directional ADX/unified, which have no option legs)."""
    atm = m.get("atm")
    step = m.get("step")
    if strategy_type == "straddle" and atm is not None:
        return [
            {"strike": atm, "option_type": "CE", "action": "SELL", "price": m.get("ce_ltp")},
            {"strike": atm, "option_type": "PE", "action": "SELL", "price": m.get("pe_ltp")},
        ]
    if strategy_type == "strangle" and atm is not None and step is not None:
        return [
            {"strike": snap(atm + step, step), "option_type": "CE", "action": "SELL", "price": m.get("otm_ce")},
            {"strike": snap(atm - step, step), "option_type": "PE", "action": "SELL", "price": m.get("otm_pe")},
        ]
    return []


def _build_signals(m: dict, parity_pct: float, history_days: int, generated_at: str, run_id: str) -> List[dict]:
    """Emit one accepted signal per qualifying strategy for this symbol:
    straddle (parity), strangle (parity + both OTM legs), adx_direction
    (ADX+candle both), unified (>=1 pass, carrying the tier). Each carries a
    stable `signal_id` and its option `legs` so the frontend can deep-link to a
    chartable detail view."""
    out: List[dict] = []

    def make(strategy_type: str) -> dict:
        legs = _legs_for(strategy_type, m)
        return {
            "strategy_type": strategy_type,
            "symbol": m["symbol"],
            "expiry": m["expiry"],
            "status": "accepted",
            "signal_id": f"strstra-{run_id[:8]}-{m['symbol'].lower()}-{strategy_type}",
            "signal_json": json.dumps({**m, "legs": legs}, default=str),
            "decision_json": json.dumps(_decision(m, parity_pct, history_days)),
            "reason": _reason(strategy_type, m),
            "generated_at": generated_at,
        }

    if m["straddle_pass"]:
        out.append(make("straddle"))
    if m["straddle_pass"] and m.get("otm_ce") and m.get("otm_pe"):
        out.append(make("strangle"))
    if m["adx_pass"] and m["candle_pass"]:
        out.append(make("adx_direction"))
    if m["n_pass"] >= 1:
        out.append(make("unified"))
    return out


def _insert_run(conn, run_id: str, started_at: datetime, data_source: Optional[str], status: str, message: str, signal_count: int) -> None:
    conn.execute(
        "INSERT INTO screener_runs (run_id, started_at, data_source, status, message, signal_count) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, started_at.isoformat(), data_source, status, message, signal_count),
    )


def _insert_signal(conn, run_id: str, sig: dict, data_source: str) -> None:
    conn.execute(
        """
        INSERT INTO screener_signals
        (run_id, signal_id, strategy_type, symbol, expiry, status, reject_reason, generated_at, data_source, reason, reason_source, signal_json, decision_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            sig["signal_id"],
            sig["strategy_type"],
            sig["symbol"],
            sig["expiry"],
            sig["status"],
            None,
            sig["generated_at"],
            data_source,
            sig["reason"],
            "template",
            sig["signal_json"],
            sig["decision_json"],
        ),
    )


async def _fetch_quotes(client, rows: List[dict], symbol: str, expiry: date, step: float) -> dict:
    """Pass 3 live quotes from Definedge, fetched serially (per symbol) to
    respect the broker's rate limits. The futures LTP fixes the ATM strike;
    ATM CE/PE + one-step OTM CE/PE are then quoted. Returns
    {"fut_ltp","ce","pe","otm_ce","otm_pe"} with None for any leg that
    couldn't be resolved or quoted."""
    from app.integrations.instruments import find_stock_future, find_stock_option

    empty = {"fut_ltp": None, "ce": None, "pe": None, "otm_ce": None, "otm_pe": None}
    try:
        fut_c = find_stock_future(rows, symbol, expiry)
        fut = float((await client.get_quote(fut_c.exchange, fut_c.token))["ltp"])
    except Exception as exc:  # noqa: BLE001 — futures quote unavailable
        logger.warning("str-stra: futures quote failed for %s (%s)", symbol, type(exc).__name__)
        return empty
    if not fut or fut <= 0:
        return empty

    atm = snap(fut, step)

    async def ltp(strike: float, option_type: str) -> Optional[float]:
        try:
            c = find_stock_option(rows, symbol, expiry, strike, option_type)
            val = float((await client.get_quote(c.exchange, c.token))["ltp"])
            return val if val and val > 0 else None
        except Exception as exc:  # noqa: BLE001 — one failing leg shouldn't abort the symbol
            logger.warning("str-stra: option quote failed for %s %s %s (%s)", symbol, strike, option_type, type(exc).__name__)
            return None

    ce = await ltp(atm, "CE")
    pe = await ltp(atm, "PE")
    otm_ce = await ltp(snap(atm + step, step), "CE")
    otm_pe = await ltp(snap(atm - step, step), "PE")
    return {"fut_ltp": fut, "ce": ce, "pe": pe, "otm_ce": otm_ce, "otm_pe": otm_pe}


async def run_str_stra_job() -> None:
    if not settings.str_stra_enabled:
        return

    from app.api.routes.auth import _client, ensure_authenticated
    from app.integrations import instruments
    from app.integrations.definedge_client import DefinedgeAuthError

    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    parity_pct = settings.str_stra_atm_parity_pct
    history_days = settings.str_stra_history_days

    def degraded(message: str) -> None:
        _set_status("degraded", message)
        with get_conn() as conn:
            _insert_run(conn, run_id, started_at, None, "degraded", message, 0)
        logger.warning("str-stra run degraded: %s", message)

    try:
        await ensure_authenticated()
        rows = await instruments.get_master_rows(_client)
    except DefinedgeAuthError as exc:
        degraded(f"Definedge not authenticated — str-stra screener paused ({exc})")
        return
    except Exception as exc:  # noqa: BLE001 — master download failure
        degraded(f"Definedge instrument master unavailable — str-stra screener paused ({type(exc).__name__})")
        return

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=history_days)

    try:
        async def fetch_history(sym: str, lot: int, step: float):
            try:
                eq = instruments.find_stock_equity(rows, sym)
                candles = await _client.get_historical_candles(eq.exchange, eq.token, "day", start_dt, end_dt)
                return sym, lot, step, candles
            except Exception as exc:  # noqa: BLE001 — per-symbol fetch failure is non-fatal
                logger.warning("str-stra: history fetch failed for %s (%s)", sym, type(exc).__name__)
                return sym, lot, step, None

        fetched = await asyncio.gather(*(fetch_history(sym, lot, step) for sym, lot, step in WATCHLIST))

        histories = [c for _, _, _, c in fetched if c]
        if not histories:
            # Definedge returned no daily candles for the whole watchlist —
            # record a degraded run rather than fabricating an empty "ok".
            degraded("Definedge returned no daily history for any watchlist symbol")
            return

        signals: List[dict] = []
        for sym, lot, step, candles in fetched:
            if not candles:
                continue
            tech = technical_metrics(candles)
            if tech is None:
                continue
            try:
                expiry = instruments.list_future_expiries(rows, sym, date.today())[0]
            except IndexError:
                logger.warning("str-stra: no future expiry for %s — skipped", sym)
                continue
            try:
                quotes = await _fetch_quotes(_client, rows, sym, expiry, step)
            except Exception as exc:  # noqa: BLE001
                logger.warning("str-stra: quote fetch failed for %s (%s)", sym, type(exc).__name__)
                continue
            st = straddle_metrics(quotes, step, lot, parity_pct)
            n_pass, tier = tier_of(tech["adx_pass"], tech["candle_pass"], st["straddle_pass"])
            m = {
                **tech,
                **st,
                "symbol": sym,
                "lot": lot,
                "step": step,
                "expiry": expiry.isoformat(),
                "n_pass": n_pass,
                "tier": tier,
            }
            signals.extend(_build_signals(m, parity_pct, history_days, started_at.isoformat(), run_id))

        with get_conn() as conn:
            _insert_run(conn, run_id, started_at, "live", "ok", f"{len(signals)} str-stra signals from Definedge", len(signals))
            for sig in signals:
                _insert_signal(conn, run_id, sig, "live")

        _set_status("ok", f"{len(signals)} str-stra signals from Definedge data")
        logger.info("str-stra run produced %d signals across %d symbols", len(signals), len(WATCHLIST))
    except Exception as exc:  # noqa: BLE001 — any fatal failure → degraded, no signals
        degraded(f"str-stra screener paused ({type(exc).__name__})")
