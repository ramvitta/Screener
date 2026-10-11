"""
Periodic screener run (SRS section 4.5/4.6). Queries ClickHouse for the
latest option chain snapshot and underlying candles, runs the Phase 1
screener engine, and caches results for the /api/v1/screener/signals
endpoint and /ws/alerts push.

The deterministic sample chain is a development fixture only: it is used
solely when ALLOW_SAMPLE_DATA is true. Otherwise, when no real data is
available, the run emits NO signals and reports a degraded status — the
screener never presents fabricated market data as a recommendation.
"""

import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.config import settings
from app.db.clickhouse import get_client
from app.db.sqlite import get_conn
from app.integrations.definedge_client import DefinedgeAuthError
from app.models.schemas import DataSource
from app.reasoning.generator import enrich_reasons
from app.screener.engine import ChainRow, OptionChainSnapshot, ScreenerParams, run_screener
from app.screener.pricing import MIN_T_YEARS, time_to_expiry_years

logger = logging.getLogger(__name__)

_latest_signals: List[dict] = []
_last_success_at: Optional[datetime] = None
_status: Dict[str, object] = {"state": "starting", "data_source": None, "message": "Screener has not run yet", "last_run": None}

# ATM ± this many 50-point strikes, each side. Needs to be wide enough that
# both the screener's target short-leg delta (0.10-0.30) AND that leg's
# spread-width-further wing (see ScreenerParams.spread_width) fall inside the
# fetched chain — near a weekly expiry, deltas move fast per strike, so a
# short leg at delta ~0.20 can sit several hundred points from ATM.
NIFTY_STRIKE_BAND = 12


def get_latest_signals() -> List[dict]:
    """Withholds signals that are older than MAX_SIGNAL_AGE — stale
    recommendations are worse than none."""
    if _last_success_at is None:
        return []
    age = (datetime.now(timezone.utc) - _last_success_at).total_seconds()
    if age > settings.max_signal_age_seconds:
        return []
    return _latest_signals


def get_status() -> Dict[str, object]:
    return dict(_status)


def _set_status(state: str, data_source: Optional[str], message: str) -> None:
    _status.update(state=state, data_source=data_source, message=message, last_run=datetime.now(timezone.utc).isoformat())


def _sample_snapshot() -> OptionChainSnapshot:
    """Deterministic fallback chain, used until real ClickHouse data exists."""
    spot = 21834.5
    expiry = date.today() + timedelta(days=((4 - date.today().weekday()) % 7 or 7))
    chain = [
        ChainRow(strike=21600, option_type="PE", ltp=14, iv=0.16, delta=-0.10),
        ChainRow(strike=21700, option_type="PE", ltp=27, iv=0.152, delta=-0.19),
        ChainRow(strike=21750, option_type="PE", ltp=35, iv=0.148, delta=-0.22),
        ChainRow(strike=21800, option_type="PE", ltp=45, iv=0.148, delta=-0.25),
        ChainRow(strike=22000, option_type="CE", ltp=45, iv=0.141, delta=0.20),
        ChainRow(strike=22100, option_type="CE", ltp=23, iv=0.136, delta=0.11),
        ChainRow(strike=22150, option_type="CE", ltp=15, iv=0.132, delta=0.08),
        ChainRow(strike=22200, option_type="CE", ltp=10, iv=0.128, delta=0.05),
    ]
    return OptionChainSnapshot(
        symbol="NIFTY",
        expiry=expiry,
        spot=spot,
        t_years=5 / 365,
        chain=chain,
        underlying_closes=[21700 + i * 3 for i in range(60)],
        historical_iv=[0.11 + 0.001 * i for i in range(120)],
        data_source=DataSource.sample,
    )


def _load_chain_from_clickhouse(symbol: str = "NIFTY") -> Optional[OptionChainSnapshot]:
    try:
        client = get_client()
        result = client.query(
            """
            SELECT strike, option_type, close AS ltp, expiry
            FROM options_1min_candles
            WHERE symbol = {symbol:String}
            ORDER BY ts DESC
            LIMIT 200
            """,
            parameters={"symbol": symbol},
        )
        if not result.result_rows:
            return None
        # Real implementation: derive IV via pricing.implied_volatility() per row,
        # compute delta via pricing.greeks(), and pull underlying_closes/historical_iv
        # from a companion index-candles table. Omitted here pending live data.
        return None
    except Exception as exc:  # ClickHouse not reachable yet
        logger.warning("ClickHouse unavailable, using sample chain: %s", exc)
        return None


async def _live_snapshot_from_definedge(symbol: str = "NIFTY") -> Optional[OptionChainSnapshot]:
    """Builds a real option-chain snapshot from live Definedge quotes: the
    nearest *unexpired* expiry, real ATM ± NIFTY_STRIKE_BAND strikes, and
    each leg's live premium/IV/delta. Unlike _sample_snapshot(), every strike
    and the expiry here are guaranteed to be real, currently-listed
    contracts, so a signal generated from this path always resolves
    correctly in GET/POST /api/v1/charts/* afterwards."""
    from app.api.routes.auth import _client, ensure_authenticated
    from app.integrations import instruments
    from app.screener import pricing

    try:
        await ensure_authenticated()
        rows = await instruments.get_master_rows(_client)

        spot_contract = instruments.find_spot(rows, symbol)
        spot_quote = await _client.get_quote(spot_contract.exchange, spot_contract.token)
        spot = float(spot_quote["ltp"])
        if not math.isfinite(spot) or spot <= 0:
            logger.warning("live_snapshot: implausible spot quote, aborting")
            return None
        atm_strike = round(spot / instruments.NIFTY_STRIKE_INTERVAL) * instruments.NIFTY_STRIKE_INTERVAL

        today = date.today()
        expiry = instruments.nearest_expiry(rows, symbol, today)
        t_years = time_to_expiry_years(expiry)
        if t_years < MIN_T_YEARS:
            logger.info("live_snapshot: nearest expiry %s is within the final hour (or past) — no signals", expiry)
            return None

        chain: List[ChainRow] = []
        skipped = 0
        for offset in range(-NIFTY_STRIKE_BAND, NIFTY_STRIKE_BAND + 1):
            strike = atm_strike + offset * instruments.NIFTY_STRIKE_INTERVAL
            for option_type in ("CE", "PE"):
                try:
                    contract = instruments.find_option(rows, symbol, expiry, strike, option_type)
                    quote = await _client.get_quote(contract.exchange, contract.token)
                    ltp = float(quote["ltp"])
                except Exception:  # noqa: BLE001 — best-effort per strike (not found / quote failure)
                    skipped += 1
                    continue
                if not math.isfinite(ltp) or ltp <= 0:
                    skipped += 1
                    continue
                iv = pricing.implied_volatility(ltp, spot, strike, t_years, option_type)
                if iv <= 0:
                    continue
                delta = pricing.greeks(spot, strike, t_years, iv, option_type).delta
                chain.append(ChainRow(strike=strike, option_type=option_type, ltp=ltp, iv=iv, delta=delta))

        if skipped:
            logger.warning("live_snapshot: %d strikes skipped (missing contract, failed quote, or no IV)", skipped)
        if not chain:
            return None

        # Real IV-rank needs a backfilled multi-month history (SRS §4.1),
        # which ClickHouse doesn't have yet. No history is fabricated: the
        # engine reports IV rank as unavailable and skips that filter.
        historical_iv: List[float] = []

        underlying_closes = [spot]
        try:
            future = instruments.find_future(rows, symbol, today)
            end = datetime.now().replace(second=0, microsecond=0)
            start = end - timedelta(hours=6)
            candles = await _client.get_historical_candles(future.exchange, future.token, "minute", start, end)
            if candles:
                underlying_closes = [c["close"] for c in candles]
        except Exception as exc:  # noqa: BLE001 — RSI/S-R just fall back to a flat series
            logger.warning("live_snapshot: underlying candle fetch failed (%s), using spot only", type(exc).__name__)

        return OptionChainSnapshot(
            symbol=symbol,
            expiry=expiry,
            spot=spot,
            t_years=t_years,
            chain=chain,
            underlying_closes=underlying_closes,
            historical_iv=historical_iv,
            data_source=DataSource.live,
        )
    except DefinedgeAuthError:
        logger.info("live_snapshot: Definedge not authenticated")
        return None
    except Exception as exc:  # noqa: BLE001 — any resolution failure degrades the run
        logger.warning("live_snapshot: failed to build live chain (%s: %s)", type(exc).__name__, exc)
        return None


async def run_screener_job() -> None:
    global _latest_signals, _last_success_at
    snapshot = _load_chain_from_clickhouse()
    if snapshot is not None:
        snapshot.data_source = DataSource.clickhouse
    else:
        snapshot = await _live_snapshot_from_definedge()
    if snapshot is None:
        if not settings.allow_sample_data:
            _latest_signals = []
            _set_status("degraded", None, "No live market data available — screener paused (sample data is disabled)")
            logger.warning("Screener run skipped: no real market data and ALLOW_SAMPLE_DATA is off")
            return
        snapshot = _sample_snapshot()

    # The sample fixture's illustrative IV band can't meet the production
    # IV-rank threshold, so dev runs relax it. Real data uses full defaults.
    params = ScreenerParams(min_iv_rank=0.0) if snapshot.data_source == DataSource.sample else None
    signals = run_screener(snapshot, params)
    signals = await enrich_reasons(signals, snapshot)  # no-op unless LLM_REASON_ENABLED
    _latest_signals = [s.model_dump(mode="json") for s in signals]
    _last_success_at = datetime.now(timezone.utc)
    if snapshot.data_source == DataSource.sample:
        _set_status("sample", "sample", "Showing SAMPLE data — not real market prices")
    else:
        _set_status("ok", snapshot.data_source.value, f"{len(signals)} signals from {snapshot.data_source.value} data")

    with get_conn() as conn:
        for signal in signals:
            conn.execute(
                """
                INSERT INTO alerts (alert_type, strategy_type, symbol, payload_json)
                VALUES ('screener_signal', ?, ?, ?)
                """,
                (
                    signal.strategy_type.value,
                    signal.symbol,
                    json.dumps(signal.model_dump(mode="json")),
                ),
            )

    logger.info("Screener run produced %d signals", len(signals))
