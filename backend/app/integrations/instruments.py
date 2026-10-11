"""
Resolves real Definedge instrument tokens — NIFTY near-month future, NIFTY 50
spot index, and specific option contracts (either the ATM strike, or an
arbitrary strike/expiry pulled from a screener signal's legs) — from
Definedge's public instrument master (allmaster.zip). Field layout verified
against a real download: see DefinedgeClient.get_instrument_master().
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional

from app.integrations.definedge_client import DefinedgeClient

NIFTY_STRIKE_INTERVAL = 50

_master_cache: dict = {"rows": None, "loaded_at": None}
_CACHE_TTL = timedelta(hours=12)


@dataclass
class ResolvedContract:
    exchange: str
    token: str
    trading_symbol: str
    expiry: Optional[date] = None
    strike: Optional[float] = None


@dataclass
class NiftyAtmContracts:
    spot: ResolvedContract
    future: ResolvedContract
    ce: ResolvedContract
    pe: ResolvedContract
    atm_strike: float
    options_expiry: date


class ContractNotFound(Exception):
    pass


def _parse_expiry(ddmmyyyy: str) -> date:
    return datetime.strptime(ddmmyyyy, "%d%m%Y").date()


async def get_master_rows(client: DefinedgeClient) -> List[dict]:
    now = datetime.now()
    if _master_cache["rows"] is not None and now - _master_cache["loaded_at"] < _CACHE_TTL:
        return _master_cache["rows"]
    rows = await client.get_instrument_master()
    _master_cache["rows"] = rows
    _master_cache["loaded_at"] = now
    return rows


def find_spot(rows: List[dict], symbol: str = "NIFTY") -> ResolvedContract:
    trading_symbol = "Nifty 50" if symbol == "NIFTY" else symbol
    row = next(
        r for r in rows if r["segment"] == "NSE" and r["instrument_type"] == "IDX" and r["trading_symbol"] == trading_symbol
    )
    return ResolvedContract(exchange="NSE", token=row["token"], trading_symbol=row["trading_symbol"])


def find_future(rows: List[dict], symbol: str, on_or_after: date) -> ResolvedContract:
    future_rows = [r for r in rows if r["segment"] == "NFO" and r["symbol"] == symbol and r["instrument_type"] == "FUTIDX"]
    try:
        row = min(
            (r for r in future_rows if _parse_expiry(r["expiry"]) >= on_or_after),
            key=lambda r: _parse_expiry(r["expiry"]),
        )
    except ValueError:
        raise ContractNotFound(f"No {symbol} future found on/after {on_or_after}") from None
    return ResolvedContract(
        exchange="NFO", token=row["token"], trading_symbol=row["trading_symbol"], expiry=_parse_expiry(row["expiry"])
    )


def find_option(rows: List[dict], symbol: str, expiry: date, strike: float, option_type: str) -> ResolvedContract:
    try:
        row = next(
            r
            for r in rows
            if r["segment"] == "NFO"
            and r["symbol"] == symbol
            and r["instrument_type"] == "OPTIDX"
            and r["option_type"] == option_type
            and _parse_expiry(r["expiry"]) == expiry
            and float(r["strike"]) == strike
        )
    except StopIteration:
        raise ContractNotFound(f"No {symbol} {option_type} {strike} contract for expiry {expiry}") from None
    return ResolvedContract(
        exchange="NFO", token=row["token"], trading_symbol=row["trading_symbol"], expiry=expiry, strike=strike
    )


def nearest_expiry(rows: List[dict], symbol: str, on_or_after: date) -> date:
    option_rows = [r for r in rows if r["segment"] == "NFO" and r["symbol"] == symbol and r["instrument_type"] == "OPTIDX"]
    expiries = {_parse_expiry(r["expiry"]) for r in option_rows if _parse_expiry(r["expiry"]) >= on_or_after}
    if not expiries:
        raise ContractNotFound(f"No {symbol} option expiries found on/after {on_or_after}")
    return min(expiries)


# --- Stock (F&O) helpers, for the admin historical-data-download page ---
# Confirmed against a real instrument-master download (see
# backend/scripts/stock_history_probe.py): stock options are segment NFO /
# instrument_type OPTSTK, stock futures are NFO / FUTSTK, and cash equity is
# segment NSE / instrument_type EQ with trading_symbol "<SYMBOL>-EQ" (not a
# bare symbol match, unlike the index spot lookup in find_spot()).


def find_stock_equity(rows: List[dict], symbol: str) -> ResolvedContract:
    try:
        row = next(r for r in rows if r["segment"] == "NSE" and r["instrument_type"] == "EQ" and r["trading_symbol"] == f"{symbol}-EQ")
    except StopIteration:
        raise ContractNotFound(f"No NSE equity listing found for {symbol}") from None
    return ResolvedContract(exchange="NSE", token=row["token"], trading_symbol=row["trading_symbol"])


def list_fno_stocks(rows: List[dict]) -> List[dict]:
    """Distinct F&O stock symbols with lot size and nearest future expiry —
    for the admin page's stock picker."""
    by_symbol: dict = {}
    for r in rows:
        if r["segment"] != "NFO" or r["instrument_type"] not in ("OPTSTK", "FUTSTK"):
            continue
        entry = by_symbol.setdefault(r["symbol"], {"symbol": r["symbol"], "lot_size": int(r["lot_size"]), "expiries": set()})
        entry["expiries"].add(r["expiry"])
    out = []
    for symbol, entry in sorted(by_symbol.items()):
        expiries = sorted(_parse_expiry(e) for e in entry["expiries"])
        out.append({"symbol": symbol, "lot_size": entry["lot_size"], "nearest_expiry": expiries[0] if expiries else None})
    return out


def list_future_expiries(rows: List[dict], symbol: str, on_or_after: date) -> List[date]:
    rows = [r for r in rows if r["segment"] == "NFO" and r["symbol"] == symbol and r["instrument_type"] == "FUTSTK"]
    return sorted({e for r in rows if (e := _parse_expiry(r["expiry"])) >= on_or_after})


def list_option_expiries(rows: List[dict], symbol: str, on_or_after: date) -> List[date]:
    rows = [r for r in rows if r["segment"] == "NFO" and r["symbol"] == symbol and r["instrument_type"] == "OPTSTK"]
    return sorted({e for r in rows if (e := _parse_expiry(r["expiry"])) >= on_or_after})


def find_stock_future(rows: List[dict], symbol: str, expiry: date) -> ResolvedContract:
    try:
        row = next(
            r for r in rows
            if r["segment"] == "NFO" and r["symbol"] == symbol and r["instrument_type"] == "FUTSTK" and _parse_expiry(r["expiry"]) == expiry
        )
    except StopIteration:
        raise ContractNotFound(f"No {symbol} future for expiry {expiry}") from None
    return ResolvedContract(exchange="NFO", token=row["token"], trading_symbol=row["trading_symbol"], expiry=expiry)


def find_stock_option(rows: List[dict], symbol: str, expiry: date, strike: float, option_type: str) -> ResolvedContract:
    try:
        row = next(
            r for r in rows
            if r["segment"] == "NFO" and r["symbol"] == symbol and r["instrument_type"] == "OPTSTK"
            and r["option_type"] == option_type and _parse_expiry(r["expiry"]) == expiry and float(r["strike"]) == strike
        )
    except StopIteration:
        raise ContractNotFound(f"No {symbol} {option_type} {strike} contract for expiry {expiry}") from None
    return ResolvedContract(exchange="NFO", token=row["token"], trading_symbol=row["trading_symbol"], expiry=expiry, strike=strike)


def list_strikes_near(rows: List[dict], symbol: str, expiry: date, option_type: str, spot: float, band: int) -> List[float]:
    """The `band` nearest strikes to spot for this symbol/expiry/option_type
    — downloading every strike (a liquid stock can have 100+) isn't useful;
    most OI sits near the money."""
    strikes = sorted(
        {
            float(r["strike"])
            for r in rows
            if r["segment"] == "NFO" and r["symbol"] == symbol and r["instrument_type"] == "OPTSTK"
            and r["option_type"] == option_type and _parse_expiry(r["expiry"]) == expiry
        },
        key=lambda s: abs(s - spot),
    )
    return sorted(strikes[:band])


async def resolve_nifty_atm_contracts(client: DefinedgeClient) -> NiftyAtmContracts:
    rows = await get_master_rows(client)
    today = date.today()

    spot = find_spot(rows, "NIFTY")
    quote = await client.get_quote(spot.exchange, spot.token)
    ltp = float(quote["ltp"])
    atm_strike = round(ltp / NIFTY_STRIKE_INTERVAL) * NIFTY_STRIKE_INTERVAL

    future = find_future(rows, "NIFTY", today)
    options_expiry = nearest_expiry(rows, "NIFTY", today)

    return NiftyAtmContracts(
        spot=spot,
        future=future,
        ce=find_option(rows, "NIFTY", options_expiry, atm_strike, "CE"),
        pe=find_option(rows, "NIFTY", options_expiry, atm_strike, "PE"),
        atm_strike=atm_strike,
        options_expiry=options_expiry,
    )
