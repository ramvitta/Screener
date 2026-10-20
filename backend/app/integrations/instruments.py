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
from app.integrations.telegram_calls_parser import resolve_alias

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
    # Instrument-master metadata, shown beside a tracked call's chart. Optional
    # because the call chart still works if a broker master row omits a field.
    instrument_type: Optional[str] = None
    underlying: Optional[str] = None
    lot_size: Optional[int] = None
    tick_size: Optional[float] = None


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


def _optional_int(value) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _optional_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _contract_from_row(
    row: dict,
    *,
    expiry: Optional[date] = None,
    strike: Optional[float] = None,
) -> ResolvedContract:
    """Build a resolved contract with the master fields the UI can explain.

    A row's expiry is the broker's ``DDMMYYYY`` string; callers that already
    parsed it pass it in. Equity/spot rows have no expiry, so it stays None.
    """
    instrument_type = row.get("instrument_type")
    contract_expiry = expiry
    # Cash equity and index spot carry a placeholder 01-Jan-1980 expiry in the
    # master; they are not dated contracts, so do not show a bogus expiry.
    if instrument_type in ("EQ", "IDX"):
        contract_expiry = None
    elif contract_expiry is None and row.get("expiry"):
        try:
            contract_expiry = _parse_expiry(row["expiry"])
        except (TypeError, ValueError):
            contract_expiry = None
    # Definedge's master stores tick size in paise (5 => ₹0.05, 10 => ₹0.10).
    tick_paise = _optional_float(row.get("tick_size"))
    return ResolvedContract(
        exchange=row["segment"],
        token=row["token"],
        trading_symbol=row["trading_symbol"],
        expiry=contract_expiry,
        strike=strike,
        instrument_type=instrument_type,
        underlying=row.get("symbol"),
        lot_size=_optional_int(row.get("lot_size")),
        tick_size=tick_paise / 100 if tick_paise is not None else None,
    )


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
    return _contract_from_row(row)


def find_future(rows: List[dict], symbol: str, on_or_after: date) -> ResolvedContract:
    future_rows = [r for r in rows if r["segment"] == "NFO" and r["symbol"] == symbol and r["instrument_type"] == "FUTIDX"]
    try:
        row = min(
            (r for r in future_rows if _parse_expiry(r["expiry"]) >= on_or_after),
            key=lambda r: _parse_expiry(r["expiry"]),
        )
    except ValueError:
        raise ContractNotFound(f"No {symbol} future found on/after {on_or_after}") from None
    return _contract_from_row(row, expiry=_parse_expiry(row["expiry"]))


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
    return _contract_from_row(row, expiry=expiry, strike=strike)


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
    """Resolve a cash-equity symbol to its NSE listing.

    The telegram parser stores the full space-stripped company name (e.g.
    "ABBINDIA", "CARTRADETECH") while the NSE master uses a shorter ticker
    ("ABB", "CARTRADE"). An exact `"<SYMBOL>-EQ"` match therefore fails for
    most equity calls. Fall back to a prefix match — the ticker is almost
    always a prefix of the full name (or vice-versa for truncated names like
    "BLUESTAR" -> "BLUESTARCO") — and prefer the longest ticker so a short
    ticker (e.g. "ABB") can't shadow a more specific one. A minimum length
    guards against 1–2 character parser garbage ("BN", "MM").
    """
    eq_rows = [r for r in rows if r["segment"] == "NSE" and r["instrument_type"] == "EQ"]
    row = next((r for r in eq_rows if r["trading_symbol"] == f"{symbol}-EQ"), None)
    if row is None and len(symbol) >= 3:
        prefixed = [
            r for r in eq_rows
            if len(r["symbol"]) >= 3
            and (r["symbol"].startswith(symbol) or symbol.startswith(r["symbol"]))
        ]
        if prefixed:
            row = max(prefixed, key=lambda r: len(r["symbol"]))
    if row is None:
        raise ContractNotFound(f"No NSE equity listing found for {symbol}")
    return _contract_from_row(row)


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
    return _contract_from_row(row, expiry=expiry)


def find_stock_option(rows: List[dict], symbol: str, expiry: date, strike: float, option_type: str) -> ResolvedContract:
    try:
        row = next(
            r for r in rows
            if r["segment"] == "NFO" and r["symbol"] == symbol and r["instrument_type"] == "OPTSTK"
            and r["option_type"] == option_type and _parse_expiry(r["expiry"]) == expiry and float(r["strike"]) == strike
        )
    except StopIteration:
        raise ContractNotFound(f"No {symbol} {option_type} {strike} contract for expiry {expiry}") from None
    return _contract_from_row(row, expiry=expiry, strike=strike)


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


# --- Generic resolver for tracked Telegram calls ---------------------------
# Calls come from prose, not from an instrument picker: the expiry may be a
# month ("NATURALGAS OCT 295 CE") or a full date, and the exchange/segment may
# need to be inferred. These helpers try the precise contract first, then the
# same month/year, before giving up (the tracker records "unresolved" rather
# than guessing a different strike).

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _month_key(value: Optional[str], fallback: Optional[date] = None) -> Optional[tuple]:
    if value:
        text = value.strip().upper().replace(".", "")
        for name, month in _MONTHS.items():
            if name in text:
                year = fallback.year if fallback else date.today().year
                return (year, month)
    if fallback:
        return (fallback.year, fallback.month)
    return None


def _pick_expiry(rows: List[dict], key: Optional[tuple], posted: Optional[date] = None) -> Optional[date]:
    expiries = sorted({_parse_expiry(r["expiry"]) for r in rows if r.get("expiry")})
    if not expiries:
        return None
    if key:
        same = [e for e in expiries if (e.year, e.month) == key]
        if same:
            return same[-1] if posted is None else min(same, key=lambda e: abs((e - posted).days))
    if posted:
        future = [e for e in expiries if e >= posted]
        if future:
            return future[0]
    return expiries[0]


def find_contract_for_call(
    rows: List[dict],
    *,
    symbol: str,
    segment: str,
    expiry: Optional[date] = None,
    expiry_text: Optional[str] = None,
    strike: Optional[float] = None,
    option_type: Optional[str] = None,
    posted: Optional[date] = None,
) -> ResolvedContract:
    """Best-effort contract lookup for a prose/LLM-extracted call.

    Raises ContractNotFound when the symbol/contract genuinely isn't in the
    master. Same-month expiry fallback is intentional for commodity formats
    that only name the month.
    """
    symbol = resolve_alias(symbol.upper())
    month_key = _month_key(expiry_text, expiry)

    def _options(instrument_types: tuple, segments: tuple) -> List[dict]:
        return [
            r for r in rows
            if r["segment"] in segments
            and r["instrument_type"] in instrument_types
            and r["symbol"] == symbol
            and r.get("option_type") == option_type
            and strike is not None
            and float(r.get("strike") or 0) == float(strike)
        ]

    def _futures(instrument_types: tuple, segments: tuple) -> List[dict]:
        return [
            r for r in rows
            if r["segment"] in segments
            and r["instrument_type"] in instrument_types
            and r["symbol"] == symbol
        ]

    def _resolve(candidates: List[dict], want_option: bool = False) -> ResolvedContract:
        if not candidates:
            raise ContractNotFound(f"No contract for {symbol} {segment}")
        exact = [r for r in candidates if expiry and _parse_expiry(r["expiry"]) == expiry]
        chosen = exact[0] if exact else None
        if chosen is None:
            picked = _pick_expiry(candidates, month_key, posted)
            if picked is None:
                raise ContractNotFound(f"No expiry for {symbol} {segment}")
            chosen = next(r for r in candidates if _parse_expiry(r["expiry"]) == picked)
        return _contract_from_row(
            chosen,
            expiry=_parse_expiry(chosen["expiry"]),
            strike=float(chosen["strike"]) if want_option and chosen.get("strike") else None,
        )

    if segment == "equity" or (segment == "other" and not option_type):
        try:
            return find_stock_equity(rows, symbol)
        except ContractNotFound:
            spot_symbol = "Nifty 50" if symbol == "NIFTY" else symbol
            row = next(
                (r for r in rows if r["segment"] == "NSE" and r["instrument_type"] == "IDX" and r["trading_symbol"] == spot_symbol),
                None,
            )
            if row:
                return _contract_from_row(row)
            raise

    if segment in ("index_option", "stock_option", "commodity_option") or option_type:
        if segment == "index_option":
            candidates = _options(("OPTIDX",), ("NFO",))
        elif segment == "stock_option":
            candidates = _options(("OPTSTK",), ("NFO",))
        elif segment == "commodity_option":
            candidates = _options(("OPTFUT",), ("MCX", "NCDEX"))
        else:  # segment unknown but option_type known
            candidates = _options(("OPTIDX", "OPTSTK", "OPTFUT"), ("NFO", "MCX", "NCDEX"))
        return _resolve(candidates, want_option=True)

    if segment == "index_future":
        return _resolve(_futures(("FUTIDX",), ("NFO",)))
    if segment == "stock_future":
        return _resolve(_futures(("FUTSTK",), ("NFO",)))
    if segment == "commodity_future":
        return _resolve(_futures(("FUTCOM",), ("MCX", "NCDEX")))
    if segment == "commodity":
        return _resolve(_futures(("FUTCOM",), ("MCX", "NCDEX")))
    if segment == "index":
        return find_spot(rows, symbol)
    return _resolve(_futures(("FUTIDX", "FUTSTK", "FUTCOM"), ("NFO", "MCX", "NCDEX")))
