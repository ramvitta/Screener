"""Deterministic extraction of trade calls from Telegram market messages.

This is the offline/no-LLM half of the calls tracker. It recognises the
formats Indian market channels actually use (BUY/SELL alerts, option contracts,
entry ranges, SL/TP/CMP, booked-profit / target-achieved / SL-hit follow-ups)
and returns a validated :class:`TelegramMessageExtract`.

It deliberately errs on the side of *not* calling something a trade: a message
that only mentions a company or says "selling pressure" is commentary. Numbers
are taken from the text verbatim (commas/currency stripped); the LLM pass can
complement this module but never replaces its safety properties.
"""

import re
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.models.schemas import TelegramCallExtract, TelegramEventExtract, TelegramMessageExtract

IST = ZoneInfo("Asia/Kolkata")

_MONTHS = {
    "JAN": 1, "JANUARY": 1, "FEB": 2, "FEBRUARY": 2, "MAR": 3, "MARCH": 3,
    "APR": 4, "APRIL": 4, "MAY": 5, "JUN": 6, "JUNE": 6, "JUL": 7, "JULY": 7,
    "AUG": 8, "AUGUST": 8, "SEP": 9, "SEPT": 9, "SEPTEMBER": 9, "OCT": 10,
    "OCTOBER": 10, "NOV": 11, "NOVEMBER": 11, "DEC": 12, "DECEMBER": 12,
}
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))

_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX", "NIFTY50"}
_COMMODITY_SYMBOLS = {
    "NATURALGAS", "CRUDEOIL", "CRUDE", "GOLD", "GOLDM", "GOLDPETAL", "SILVER", "SILVERM",
    "COPPER", "ZINC", "ALUMINIUM", "ALUMINUM", "LEAD", "NICKEL", "MENTHAOIL", "COTTON",
    "SOYABEAN", "SOYBEAN", "TURMERIC", "CUMIN", "CORIANDER", "GUARGUM", "GUARSEED",
    "CASTOR", "CHANA", "JEERA", "MUSTARD", "RMSEED", "CRUDEPALM", "KAPAS", "SUGAR",
    "WHEAT", "PADDY", "MAIZE", "CARDAMOM", "COFFEE", "RUBBER", "KCALRT",
}
_NCDEX_SYMBOLS = {
    "TURMERIC", "CUMIN", "CORIANDER", "GUARGUM", "GUARSEED", "CASTOR", "CHANA", "JEERA",
    "MUSTARD", "RMSEED", "CRUDEPALM", "KAPAS", "SUGAR", "WHEAT", "PADDY", "MAIZE",
    "CARDAMOM", "COFFEE", "RUBBER", "SOYABEAN", "SOYBEAN", "COTTON",
}
# Common company-name → NSE symbol aliases for formats that don't put the
# trading symbol in the message ("BUY Adani Ports & SEZ @ ...").
_ALIASES = {
    "ADANIPORTSANDSEZ": "ADANIPORTS",
    "ADANIPORTS": "ADANIPORTS",
    "BAJAJFINANCE": "BAJFINANCE",
    "HINDUSTANZINC": "HINDZINC",
    "ULTRATECHCEMENT": "ULTRACEMCO",
    "ULTRATECH": "ULTRACEMCO",
    "UNIONBANKOFINDIA": "UNIONBANK",
    "UNIONBANK": "UNIONBANK",
    "INDIANRENEWABLEENERGY": "IREDA",
    "INDIANRENEWABLEENERGYDEVELOPMENTAGENCY": "IREDA",
    "PARAGMILK": "PARAGMILK",
    "PARAGMILKAND": "PARAGMILK",
    "STATEBANKOFINDIA": "SBIN",
    "ICICIBANK": "ICICIBANK",
    "HDFCBANK": "HDFCBANK",
    "RELIANCEINDUSTRIES": "RELIANCE",
    "TATAMOTORS": "TATAMOTORS",
    "TATASTEEL": "TATASTEEL",
    "SUNPHARMA": "SUNPHARMA",
    "DRREDDYS": "DRREDDY",
    "DRREDDYSAND": "DRREDDY",
    "SONABLWPRECISION": "SONACOMS",
    "PBFINTECH": "POLICYBZR",
    "AJANTAPHARMA": "AJANTPHARM",
    "LLOYDSMETALSENERGY": "LLOYDSME",
    "INDIANHOTELSCO": "INDHOTEL",
    "KARURVYSYABANK": "KARURVYSYA",
    "AEGISLOGISTICS": "AEGISLOG",
    "RADICOKHAITAN": "RADICO",
    "FEDERALBANK": "FEDERALBNK",
    "LEMONTREEHOTELS": "LEMONTREE",
    "CCLPRODUCTSINDIA": "CCL",
    "MOTILALOSWALFIN": "MOTILALOFS",
    "SAMVARDHANAMOTHERSONINT": "MOTHERSON",
    "ACMESOLARHOLDINGS": "ACMESOLAR",
    "VARUNBEVERAGES": "VBL",
    "BELRISEINDUSTRIES": "BELRISE",
    "DEEPAKFERTPETRO": "DEEPAKFERT",
    "SYRMASGSTECHNOLOGY": "SYRMA",
    "ATHERENERGY": "ATHERENERG",
}

_ACTION_RE = re.compile(r"(?<![A-Za-z0-9])(BUY|SELL|SHORT|LONG)(?![A-Za-z0-9-])", re.I)
_NEGATION_BEFORE_RE = re.compile(
    r"(?:\bNOT|\bNO|\bNEVER|\bDON'?T|\bDO\s+NOT|\bAVOID|\bWITHOUT|\bCAN'?T|\bWON'?T|\bISN'?T|\bNOT\s+THE\s+TIME\s+TO)\s*$",
    re.I,
)
_ALERT_CONTEXT_RE = re.compile(
    r"\b(?:ALERT|CALL|INTRADAY|BTST|SWING|POSITIONAL|KCALRT|RECO|REITERATE|BOOK|EXIT|TARGET|SL|STOP\s*LOSS)\b",
    re.I,
)
_COMMON_WORDS = {
    "THE", "THIS", "THAT", "THESE", "THOSE", "IS", "ARE", "WAS", "WERE", "BE", "BEEN",
    "AT", "ON", "IN", "OF", "FOR", "TO", "FROM", "WITH", "AND", "OR", "BUT", "NOT",
    "TIME", "MARKET", "VALUATION", "LEVEL", "LEVELS", "SUPPORT", "RESISTANCE", "TARGET",
    "STOP", "LOSS", "SELL", "SELLING", "BUY", "BUYING", "SHORT", "LONG", "TRADE", "ALERT",
    "UPDATE", "CLOSED", "PROFIT", "BOOKED", "EXIT", "ABOVE", "BELOW", "NEAR", "AROUND",
    "TODAY", "TOMORROW", "YESTERDAY", "WEEK", "MONTH", "YEAR", "CMP", "TGT", "TP", "SL",
    "CRUDE", "GOLD", "SILVER", "OIL", "EQUITY", "INDEX", "INVESTORS", "CURRENTLY",
    "PERIOD", "AVERAGE", "DOUBLE", "OPPORTUNITY", "INVEST", "INVESTMENT", "PANIC", "VALUATIONS",
}
_OPTION_RE = re.compile(
    rf"(?P<prefix>[A-Za-z][A-Za-z&.\s()/-]*?)?\s*"
    rf"(?:(?P<day>\d{{1,2}})\s*[-/]?\s*)?"
    rf"(?P<month>{_MONTH_PATTERN})\s*[,']?\s*"
    rf"(?P<strike>\d[\d,]*(?:\.\d+)?)\s*"
    rf"(?P<kind>CE|PE|CALL|PUT)\b",
    re.I,
)
_OPTION_NO_MONTH_RE = re.compile(
    r"(?P<prefix>[A-Za-z][A-Za-z&.\s()/-]*?)?\s*"
    r"(?P<strike>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<kind>CE|PE|CALL|PUT)\b",
    re.I,
)
_NUM = r"(?:\d[\d,]*(?:\.\d+)?)"
_ENTRY_RANGE_RE = re.compile(
    rf"(?:@|BETWEEN|\bAT\b|\bABV\b|\bABOVE\b)\s*({_NUM})\s*(?:-|–|—|TO)\s*({_NUM})",
    re.I,
)
_ENTRY_SINGLE_RE = re.compile(rf"(?:@|\bAT\b|\bABV\b|\bABOVE\b)\s*({_NUM})", re.I)
_SL_RE = re.compile(
    rf"\b(?:SL|STOP\s*LOSS(?:\s+PRICE)?|STOPLOSS|STOP|BLW|BELOW)\b\s*[:=\-]?\s*(?:~|RS\.?|₹)?\s*({_NUM})",
    re.I,
)
_TARGET_RE = re.compile(
    rf"\b(?:TARGET\s+PRICE|TGT\d*|TARGETS?|TP)\b\s*(?:OF\s*)?[:=\-]?\s*(?:~|RS\.?|₹)?\s*({_NUM}(?:\s*[/,]\s*{_NUM})*)",
    re.I,
)
_CMP_RE = re.compile(
    rf"\b(?:CURRENT\s+MARKET\s+PRICE|CMP)\b\s*[:=\-]?\s*(?:~|RS\.?|₹)?\s*({_NUM})",
    re.I,
)
_PCT_RE = re.compile(r"\b(?:PROFIT|LOSS)\s+OF\s+([\d.]+)\s*%", re.I)

_CLOSURE_RE = re.compile(
    r"\b(?:TRADE\s+CLOSED|BOOK(?:ED)?\s*(?:&\s*)?(?:PROFIT|EXIT)|BOOK\s+PROFIT|TARGET\s+(?:OF\s+[\d.,]+\s+)?(?:ACHIEVED|ACHEIVED|HIT|DONE|REACHED)|"
    r"TARGET\s+(?:ACHIEVED|ACHEIVED)|SL\s+HIT|SL\s+TRADED\s+AT|STOP\s*LOSS\s+HIT|STOPLOSS\s+HIT|EXIT(?:ED)?\s+(?:AT|@)|CLOSED\s+FOR|"
    r"PROFIT\s+BOOKED|PARTIAL\s+PROFIT|TRAIL(?:ED)?\s+SL|MOVE\s+SL)\b",
    re.I,
)
_UPDATE_RE = re.compile(r"\b(?:UPDATE|TRAIL(?:ED)?\s+SL|MOVE\s+SL|PARTIAL\s+(?:PROFIT|EXIT)|BOOK\s+PARTIAL)\b", re.I)
_PROMO_RE = re.compile(r"\b(?:OFFER|COUPON|WEBINAR|SUBSCRIPTION|JOIN\s+(?:OUR|THE)|DISCOUNT\s+(?:ON|FOR|CODE|COUPON)|PRICE\s+INCREASE|FESTIVE)\b", re.I)
_RESULT_RE = re.compile(r"\b(?:EXPECTED\s+RESULTS|RESULTS?\s+(?:ON|TODAY|TOMORROW)|EARNINGS\s+CALENDAR)\b", re.I)
_MARKET_RE = re.compile(
    r"\b(?:NIFTY|BANKNIFTY|SENSEX|MARKET|SUPPORT|RESISTANCE|RSI|VWAP|EMA|DEMA|BREAKOUT|"
    r"BULLISH|BEARISH|SIDEWAYS|FII|DII|CRUDE|GOLD|SILVER|SECTOR)\b",
    re.I,
)

_ACTION_WORDS = {"BUY", "SELL", "SHORT", "LONG"}
_STOP_PHRASES = (
    "SAHI TRADE ALERT", "TRADE ALERT", "INTRADAY UPDATE", "INTRADAY", "UPDATE", "KCALRT",
    "TRADE CLOSED", "CLOSED FOR", "BOOK PROFIT", "BOOKED PROFIT", "TARGET", "FOR",
)


def _clean(text: str) -> str:
    value = (text or "").replace("\u200b", " ").replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{2,}", "\n\n", value)
    return value.strip()


def _action_matches(text: str) -> List[re.Match]:
    """Action words that are actually calls, not commentary.

    Rejects negated ("do not sell"), hyphenated ("sell-off") and lower-case
    prose uses. A lower-case action is accepted only at the start of a line or
    close to an alert marker.
    """
    matches: List[re.Match] = []
    for match in _ACTION_RE.finditer(text):
        word = match.group(1)
        before = text[max(0, match.start() - 40):match.start()]
        if _NEGATION_BEFORE_RE.search(before):
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        at_line_start = not text[line_start:match.start()].strip(" -*•👉:")
        if not word.isupper():
            context = text[max(0, match.start() - 80):match.start()]
            if not at_line_start and not _ALERT_CONTEXT_RE.search(context):
                continue
        matches.append(match)
    return matches


def _valid_symbol_raw(raw: str) -> bool:
    value = _clean(raw).strip(" -:;,.+#")
    if not value or len(value) > 45:
        return False
    if re.search(r"[*!?|]", value):
        return False
    tokens = value.split()
    if not 1 <= len(tokens) <= 4:
        return False
    for token in tokens:
        key = re.sub(r"[^A-Z]", "", token.upper())
        if key in _COMMON_WORDS or len(key) > 24:
            return False
    return bool(re.match(r"^[A-Za-z0-9&.'()/+-]+(?:[ \t]+[A-Za-z0-9&.'()/+-]+){0,3}$", value))


def _num(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    try:
        cleaned = str(raw).replace(",", "").replace("₹", "").strip().strip(".;:%")
        return float(cleaned)
    except ValueError:
        return None


def _posted_ist(posted_at: Optional[datetime]) -> datetime:
    if posted_at is None:
        return datetime.now(IST)
    if posted_at.tzinfo is None:
        return posted_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(IST)
    return posted_at.astimezone(IST)


def _norm_alnum(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _symbol_key(raw: str) -> Tuple[str, str]:
    """Return (normalised symbol, display name) for a raw instrument phrase.

    Examples: "MCX NATURALGAS" -> ("NATURALGAS", "NATURALGAS");
    "Adani Ports & SEZ" -> ("ADANIPORTS", "Adani Ports & SEZ");
    "NIFTY" -> ("NIFTY", "NIFTY")."""
    original = _clean(raw).strip(" -:;,.")
    text = original.upper()
    text = re.sub(r"\b(?:MCX|NCDEX|NSE|BSE|NFO|CASH|EQ|FUT|FUTURES?)\b", " ", text)
    text = re.sub(r"\b(?:BUY|SELL|SHORT|LONG)\b", " ", text)
    for phrase in _STOP_PHRASES:
        text = re.sub(rf"\b{re.escape(phrase)}\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -:;,.")
    tokens = [t for t in text.split() if t]
    if not tokens:
        return "", original

    # Known index/commodity can appear anywhere (e.g. "NIFTY CE").
    for token in tokens:
        if token in _INDEX_SYMBOLS or token in _COMMODITY_SYMBOLS:
            symbol = token
            if symbol == "CRUDE":
                symbol = "CRUDEOIL"
            return symbol, original

    alnum = _norm_alnum(" ".join(tokens))
    if alnum in _ALIASES:
        return _ALIASES[alnum], original
    # If there are several words, try progressively shorter prefixes for alias
    # matches ("Adani Ports & SEZ Ltd" -> ADANIPORTS).
    for i in range(len(tokens), 0, -1):
        prefix = _norm_alnum(" ".join(tokens[:i]))
        if prefix in _ALIASES:
            return _ALIASES[prefix], original
        if prefix in _INDEX_SYMBOLS or prefix in _COMMODITY_SYMBOLS:
            return prefix, original
    return (alnum[:24] or "UNKNOWN"), original


def _segment_for(symbol: str, option_type: Optional[str], window: str) -> str:
    is_commodity = symbol in _COMMODITY_SYMBOLS
    is_index = symbol in _INDEX_SYMBOLS
    is_future = bool(re.search(r"\b(?:FUT|FUTURES?)\b", window, re.I))
    if option_type:
        if is_commodity:
            return "commodity_option"
        if is_index:
            return "index_option"
        return "stock_option"
    if is_future:
        if is_commodity:
            return "commodity_future"
        if is_index:
            return "index_future"
        return "stock_future"
    if is_commodity:
        return "commodity"
    if is_index:
        return "index"
    return "equity"


def _exchange_for(symbol: str, segment: str) -> str:
    if segment.startswith("commodity"):
        return "NCDEX" if symbol in _NCDEX_SYMBOLS else "MCX"
    if segment.endswith("_option") or segment.endswith("_future"):
        return "NFO"
    return "NSE"


def _expiry_from_text(month_raw: str, day_raw: Optional[str], posted: datetime) -> Tuple[Optional[date], Optional[str]]:
    month = _MONTHS.get(month_raw.upper())
    if not month:
        return None, None
    if day_raw:
        day = int(day_raw)
        try:
            candidate = date(posted.year, month, day)
        except ValueError:
            return None, f"{day_raw} {month_raw}"
        if candidate < (posted.date() - timedelta(days=45)):
            try:
                candidate = date(posted.year + 1, month, day)
            except ValueError:
                return None, f"{day_raw} {month_raw}"
        return candidate, f"{day_raw} {month_raw}"
    if month == 12:
        candidate = date(posted.year, 12, 31)
    else:
        candidate = date(posted.year, month + 1, 1) - timedelta(days=1)
    return candidate, month_raw


def _parse_option(window: str, posted: datetime) -> Optional[dict]:
    match = _OPTION_RE.search(window) or _OPTION_NO_MONTH_RE.search(window)
    if not match:
        return None
    groups = match.groupdict()
    strike = _num(groups.get("strike"))
    if strike is None:
        return None
    kind = (groups.get("kind") or "").upper()
    option_type = "CE" if kind in ("CE", "CALL") else "PE"
    month_raw = groups.get("month")
    expiry, expiry_text = _expiry_from_text(month_raw, groups.get("day"), posted) if month_raw else (None, None)
    prefix = re.sub(r"^\s*(?:BUY|SELL|SHORT|LONG)\b", "", groups.get("prefix") or "", count=1, flags=re.I)
    if len(_symbol_key(prefix)[0]) < 2:
        prefix = window[: match.start()]
    symbol, display = _symbol_key(prefix)
    display = re.sub(r"^\s*(?:BUY|SELL|SHORT|LONG)\b", "", display, count=1, flags=re.I).strip()
    return {
        "symbol": symbol,
        "symbol_raw": display,
        "display_name": display,
        "option_type": option_type,
        "strike": strike,
        "expiry": expiry.isoformat() if expiry else None,
        "expiry_text": expiry_text,
    }


def _equity_symbol(window: str) -> Tuple[str, str, str]:
    text = re.sub(r"^\s*(?:BUY|SELL|SHORT|LONG)\b", "", window, count=1, flags=re.I)
    cut = re.search(
        r"(?:@|BETWEEN|\bAT\b|\bSL\b|STOP\s*LOSS|STOPLOSS|\bTGT\b|\bTARGET\b|\bTP\b|\bCMP\b|\||\n|\(|"
        r"\bFOR\b|\bON\b)",
        text,
        re.I,
    )
    if cut:
        text = text[: cut.start()]
    raw = text.strip(" -:;,.")
    symbol, display = _symbol_key(raw)
    return symbol, display, raw


def _parse_levels(window: str, is_closure: bool) -> dict:
    out: dict = {}
    range_match = _ENTRY_RANGE_RE.search(window)
    if range_match:
        low, high = _num(range_match.group(1)), _num(range_match.group(2))
        if low is not None and high is not None:
            out["entry_low"], out["entry_high"] = min(low, high), max(low, high)
            out["entry_price"] = round((low + high) / 2, 4)
    if "entry_price" not in out:
        single = _ENTRY_SINGLE_RE.search(window)
        if single:
            price = _num(single.group(1))
            if price is not None:
                out["entry_price"] = price
    sl = _SL_RE.search(window)
    if sl:
        out["stop_loss"] = _num(sl.group(1))
    targets: List[float] = []
    for m in _TARGET_RE.finditer(window):
        for part in re.split(r"[/,]", m.group(1)):
            value = _num(part)
            if value is not None and value not in targets:
                targets.append(value)
    for idx, value in enumerate(targets[:3], start=1):
        out[f"target_{idx}"] = value
    cmp_match = _CMP_RE.search(window)
    if cmp_match:
        out["cmp"] = _num(cmp_match.group(1))
    if not is_closure and out.get("entry_price") is None and out.get("cmp") is not None:
        out["entry_price"] = out["cmp"]
    return {k: v for k, v in out.items() if v is not None}


def _category(text: str) -> str:
    actions = _action_matches(text)
    if _PROMO_RE.search(text) and not actions:
        return "promo"
    if _RESULT_RE.search(text):
        return "result_calendar"
    closure = _CLOSURE_RE.search(text)
    is_update = bool(re.search(r"\bUPDATE\b", text, re.I))
    if is_update and (closure or actions or _OPTION_RE.search(text) or _OPTION_NO_MONTH_RE.search(text)):
        return "trade_closure" if closure else "trade_update"
    if closure and (actions or re.search(r"\b(?:CLOSED|BOOK|TARGET|SL|STOP)\b", text, re.I)):
        return "trade_closure"
    if actions and (
        _OPTION_RE.search(text)
        or _OPTION_NO_MONTH_RE.search(text)
        or _ENTRY_RANGE_RE.search(text)
        or _ENTRY_SINGLE_RE.search(text)
        or _SL_RE.search(text)
        or _TARGET_RE.search(text)
        or _CMP_RE.search(text)
    ):
        return "trade_alert"
    if _UPDATE_RE.search(text) and (_SL_RE.search(text) or _TARGET_RE.search(text) or _PCT_RE.search(text)):
        return "trade_update"
    if _MARKET_RE.search(text):
        return "market_commentary"
    return "other"


def _parse_alert_calls(text: str, posted: datetime) -> List[TelegramCallExtract]:
    matches = _action_matches(text)
    calls: List[TelegramCallExtract] = []
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        window = _clean(text[match.start():min(end, match.start() + 600)])
        action = "SELL" if match.group(1).upper() in ("SELL", "SHORT") else "BUY"
        direction = "long" if action == "BUY" else "short"

        option = _parse_option(window, posted)
        if option:
            symbol, raw, display = option["symbol"], option["symbol_raw"], option["display_name"]
            if not _valid_symbol_raw(display):
                continue
        else:
            symbol, display, raw = _equity_symbol(window)
            option = {}
            if not _valid_symbol_raw(raw):
                continue
        if not symbol or symbol == "UNKNOWN":
            continue
        levels = _parse_levels(window, is_closure=False)
        if not option and not any(k in levels for k in ("entry_low", "entry_high", "entry_price", "stop_loss", "target_1")):
            continue

        horizon = None
        h = re.search(r"\b(?:INTRADAY|BTST|SWING|POSITIONAL|SHORT\s*TERM|LONG\s*TERM)\b", window, re.I)
        if h:
            horizon = h.group(0).upper()

        segment = _segment_for(symbol, option.get("option_type"), window)
        if not option.get("option_type") and segment.startswith("commodity"):
            # Futures alerts commonly carry only a month ("BUY MCX COPPER JUN
            # AT ...") or encode it in the symbol ("ZINCMFEB"). Capture it as
            # a month-end expiry so distinct monthly contracts stay distinct
            # and past contracts can be closed without inventing a P&L.
            month_match = re.search(rf"\b({_MONTH_PATTERN})\b", window, re.I)
            if month_match is None:
                month_match = re.search(rf"({_MONTH_PATTERN})\d{{0,4}}$", symbol, re.I)
            if month_match:
                expiry, expiry_text = _expiry_from_text(month_match.group(1), None, posted)
                if expiry:
                    option["expiry"] = expiry.isoformat()
                    option["expiry_text"] = expiry_text
        calls.append(
            TelegramCallExtract(
                symbol=symbol,
                symbol_raw=raw or display,
                display_name=display or raw,
                segment=segment,
                exchange=_exchange_for(symbol, segment),
                direction=direction,
                action=action,
                option_type=option.get("option_type"),
                strike=option.get("strike"),
                expiry=option.get("expiry"),
                expiry_text=option.get("expiry_text"),
                horizon=horizon,
                **levels,
            )
        )
    return calls


def _event_type(text: str) -> str:
    upper = text.upper()
    if re.search(r"\b(?:SL\s+HIT|SL\s+TRADED\s+AT|STOP\s*LOSS\s+HIT|STOPLOSS\s+HIT)\b", upper):
        return "sl_hit"
    if re.search(r"\bTARGET\b.*\b(?:ACHIEVED|ACHEIVED|HIT|DONE|REACHED)\b|\bTARGET\s+(?:ACHIEVED|ACHEIVED)\b", upper):
        return "target_hit"
    if re.search(r"\b(?:TRADE\s+CLOSED|CLOSED\s+FOR|BOOK(?:ED)?\s*(?:&\s*)?(?:PROFIT|EXIT)|PROFIT\s+BOOKED|EXIT(?:ED)?\s+(?:AT|@))\b", upper):
        return "closed"
    if re.search(r"\b(?:PARTIAL\s+(?:PROFIT|EXIT)|BOOK\s+PARTIAL)\b", upper):
        return "partial_exit"
    if re.search(r"\b(?:TRAIL(?:ED)?\s+SL|MOVE\s+SL)\b", upper):
        return "trail"
    return "update"


def _parse_event(text: str, posted: datetime, category: str) -> Optional[TelegramEventExtract]:
    if category not in ("trade_closure", "trade_update"):
        return None
    window = _clean(text)
    option = _parse_option(window, posted)
    if option:
        symbol = option["symbol"]
        if not _valid_symbol_raw(option.get("display_name") or ""):
            return None
    else:
        m = re.search(
            r"(?:CLOSED\s+FOR|EXIT(?:ED)?|BOOK(?:ED)?\s+PROFIT(?:\s+IN)?|FOR)\s+(.+?)(?:\s+(?:AT|@)\s+[\d.,]+|$)",
            window,
            re.I,
        )
        raw = (m.group(1) if m else window.split("\n")[0]).strip()
        if not _valid_symbol_raw(raw):
            return None
        symbol, _display = _symbol_key(raw)
    if not symbol or symbol == "UNKNOWN":
        return None

    event_type = _event_type(window)
    price = None
    exit_match = re.search(
        r"(?:CLOSED|EXIT(?:ED)?|BOOK(?:ED)?\s+PROFIT|BOOK\s+PROFIT).*?\b(?:AT|@)\s*\(?\s*(?:CMP\s*[-: ]?\s*)?([\d.,]+)",
        window,
        re.I,
    )
    if exit_match:
        price = _num(exit_match.group(1))
    if price is None:
        at_match = re.search(r"\bAT\s+([\d.,]+)", window, re.I)
        if at_match:
            price = _num(at_match.group(1))
    if price is None:
        cmp_match = _CMP_RE.search(window)
        if cmp_match:
            price = _num(cmp_match.group(1))

    target_value = None
    target_match = re.search(r"\bTARGET\s*(?:OF\s*)?([\d.,]+)", window, re.I)
    if target_match:
        target_value = _num(target_match.group(1))
    if event_type == "sl_hit":
        sl_match = _SL_RE.search(window)
        if sl_match:
            price = price if price is not None else _num(sl_match.group(1))

    pct = _PCT_RE.search(window)
    realized_pct = _num(pct.group(1)) if pct else None
    if realized_pct is not None and re.search(r"\bLOSS\b", pct.group(0), re.I):
        realized_pct = -realized_pct

    return TelegramEventExtract(
        event_type=event_type,
        symbol=symbol,
        option_type=option.get("option_type") if option else None,
        strike=option.get("strike") if option else None,
        expiry=option.get("expiry") if option else None,
        price=price,
        target_value=target_value,
        realized_pct=realized_pct,
        note=window[:500],
    )


def parse_message(text: str, posted_at: Optional[datetime] = None) -> TelegramMessageExtract:
    """Deterministic parse of one message into a validated extraction."""
    clean = _clean(text)
    posted = _posted_ist(posted_at)
    category = _category(clean)
    calls: List[TelegramCallExtract] = []
    events: List[TelegramEventExtract] = []
    if category == "trade_alert":
        calls = _parse_alert_calls(clean, posted)
        if not calls:
            category = "market_commentary" if _MARKET_RE.search(clean) else "other"
    elif category in ("trade_closure", "trade_update"):
        event = _parse_event(clean, posted, category)
        if event:
            events = [event]
        else:
            category = "market_commentary" if _MARKET_RE.search(clean) else "other"

    summary = None
    if calls:
        parts = []
        for c in calls:
            strike = int(c.strike) if c.strike and float(c.strike).is_integer() else (c.strike or "")
            contract = f"{c.symbol} {strike} {c.option_type or ''}".strip()
            parts.append(f"{c.action} {contract} @ {c.entry_price or c.cmp or '—'} SL {c.stop_loss or '—'} TGT {c.target_1 or '—'}")
        summary = "; ".join(parts)[:500]
    elif events:
        e = events[0]
        summary = f"{e.event_type.replace('_', ' ').title()} {e.symbol}" + (f" @ {e.price}" if e.price else "")

    return TelegramMessageExtract(
        category=category,
        confidence=0.9 if calls or events else 0.4,
        summary=summary,
        is_trade_call=bool(calls),
        calls=calls,
        events=events,
    )
