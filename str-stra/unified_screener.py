"""
═══════════════════════════════════════════════════════════════════════
 UNIFIED STRADDLE / STRANGLE SCREENER  —  OpenAlgo Edition
 Author : for VS | Date: July 2026
═══════════════════════════════════════════════════════════════════════

 Three-pass pipeline — single table, single run, every 10 minutes:

   PASS 1  ADX Screener (NSE daily OHLCV)
           • ADX Trend = Neutral  (3-bar classification)
           • ADX falling  (ADX_change < 0)
           • Columns: ADX, +DI, -DI, ADX Chg%, DMI Trend, ADX Trend

   PASS 2  Candle-Volume Screener (same OHLCV data, no extra API call)
           • Candle Type = Supply  (close < open)
           • Volume % < 0  (below 20-day average)
           • Comment = "Inaction"
           • Columns: Candle Type, Strength, Size, Vol%, Comment

   PASS 3  Straddle Identification (NFO live quotes via OpenAlgo)
           • Fetch Futures LTP (current month)
           • Snap to ATM strike, fetch CE + PE quotes
           • ATM parity: |CE - PE| / midpoint ≤ ATM_PARITY_PCT (2%)
           • Columns: Fut LTP, ATM, CE, PE, Spread%, Straddle Prem,
                      Straddle Value

   SIGNAL TIERS:
     ★★★ PRIME  = all 3 passes → highest-conviction straddle entry
     ★★  READY  = ADX + Candle pass, straddle parity not yet tight
     ★   WATCH  = only 1 pass (context only)

 Requirements:
   pip install requests pandas numpy tabulate colorama schedule

 Run:  python unified_screener.py
═══════════════════════════════════════════════════════════════════════
"""

import os
import json
import time
import logging
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import requests
from tabulate import tabulate
from colorama import init, Fore, Style

warnings.filterwarnings("ignore")
init(autoreset=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("UnifiedScreener")


# ══════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

class Config:
    # ── OpenAlgo ─────────────────────────────────────────────────────
    OPENALGO_HOST     = "http://127.0.0.1:5000"
    API_KEY           = os.getenv("OPENALGO_API_KEY",
                        "5f70f56afb6677db1d95aaa85a2e639c71b7b61f32555307d3bd4fe15c83445f")

    NSE_EXCHANGE      = "NSE"
    NFO_EXCHANGE      = "NFO"
    TARGET_EXPIRY     = "28JUL2026"   # DDMONYYYY — futures + options expiry

    # ── History fetch ────────────────────────────────────────────────
    HISTORY_DAYS      = 90            # calendar days back (gives ~60 trading days)
    MAX_WORKERS       = 8             # parallel history fetches

    # ── ADX ──────────────────────────────────────────────────────────
    ADX_PERIOD        = 14
    ADX_TREND_WINDOW  = 3             # bars used for Neutral/Trending/Weakening
    ADX_PREFERRED_MAX = 25.0          # highlight ADX below this

    # ── Candle / Volume ──────────────────────────────────────────────
    AVG_PERIOD        = 20            # lookback for body-size & volume averages

    # ── Straddle ─────────────────────────────────────────────────────
    ATM_PARITY_PCT    = 2.0           # |CE-PE| / mid ≤ this % → parity pass

    # ── Scheduler ────────────────────────────────────────────────────
    RUN_INTERVAL_MIN  = 10            # re-run every N minutes (0 = once)

    # ── Watchlist: (Symbol, LotSize, StrikeStep) ─────────────────────
    WATCHLIST = [
        ("ABB",         125,   25.0),
        ("ADANIENT",    250,    5.0),
        ("ADANIPORTS", 1250,    2.5),
        ("ADANIPOWER", 2000,    2.5),
        ("ANGELONE",    250,   10.0),
        ("ASIANPAINT",  200,   10.0),
        ("AXISBANK",   1200,    5.0),
        ("BEL",        3500,    1.0),
        ("BHARTIARTL",  500,    5.0),
        ("BHEL",       4500,    1.0),
        ("BIOCON",     2500,    2.0),
        ("BSE",         375,   10.0),
        ("COALINDIA",  2100,    2.0),
        ("COLPAL",      700,    5.0),
        ("DIXON",       100,   50.0),
        ("DMART",       175,   10.0),
        ("ETERNAL",    4200,    1.0),
        ("HAL",         150,   25.0),
        ("HDFCAMC",     300,   10.0),
        ("HDFCBANK",    550,    5.0),
        ("HDFCLIFE",   1100,    5.0),
        ("HINDALCO",   1000,    2.5),
        ("HINDUNILVR",  300,   10.0),
        ("ICICIBANK",   700,    5.0),
        ("INFY",        400,   10.0),
        ("ITC",        3200,    1.0),
        ("JIOFIN",     6000,    1.0),
        ("KALYANKJIL", 2500,    2.5),
        ("LT",          150,   10.0),
        ("MARUTI",       75,  100.0),
        ("MAZDOCK",     500,    5.0),
        ("NTPC",       4500,    1.0),
        ("PFC",        3000,    2.5),
        ("POLICYBZR",   940,    5.0),
        ("POLYCAB",     175,   10.0),
        ("POWERGRID",  4500,    1.0),
        ("RELIANCE",    250,   10.0),
        ("RVNL",       7000,    1.0),
        ("SBIN",       1500,    1.0),
        ("SIEMENS",     275,   25.0),
        ("SUNPHARMA",   350,    5.0),
        ("SUZLON",    14000,    0.5),
        ("TCS",         150,   25.0),
        ("TMPV",        700,    5.0),
        ("TRENT",       375,   10.0),
        ("UNITDSPR",   1000,    5.0),
        ("WAAREEENER",  275,   10.0),
        ("WIPRO",      1500,    2.0),
        ("YESBANK",   45000,    0.1),
    ]


# ══════════════════════════════════════════════════════════════════════
#  OPENALGO CLIENT
# ══════════════════════════════════════════════════════════════════════

class OpenAlgoClient:

    def __init__(self, host: str, api_key: str):
        self.host    = host.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _post(self, endpoint: str, payload: dict, silent: bool = False) -> dict:
        payload = dict(payload)
        payload["apikey"] = self.api_key
        url = f"{self.host}{endpoint}"
        try:
            resp = self.session.post(url, json=payload, timeout=10)
            if resp.status_code == 400 and silent:
                return {}
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError:
            if not silent:
                log.error("Cannot reach OpenAlgo at %s", self.host)
            return {}
        except Exception as e:
            if not silent:
                log.error("API error [%s]: %s", endpoint, e)
            return {}

    # ── History probe (run once at startup) ──────────────────────────
    _hist_interval:  str = ""
    _hist_start_key: str = "start_date"
    _hist_end_key:   str = "end_date"

    def probe_history_api(self, symbol: str = "SBIN",
                          exchange: str = "NSE") -> bool:
        """
        Try every known interval + param-name combo until one returns data.
        Caches the working values so all subsequent get_history() calls use them.
        """
        from datetime import datetime as _dt, timedelta as _td
        end   = _dt.today().strftime("%Y-%m-%d")
        start = (_dt.today() - _td(days=30)).strftime("%Y-%m-%d")

        # (interval_value, start_param_name, end_param_name)
        candidates = [
            ("D",     "start_date", "end_date"),
            ("1D",    "start_date", "end_date"),
            ("1d",    "start_date", "end_date"),
            ("day",   "start_date", "end_date"),
            ("daily", "start_date", "end_date"),
            ("D",     "from",       "to"),
            ("D",     "from_date",  "to_date"),
            ("1d",    "from",       "to"),
            ("1",     "start_date", "end_date"),
        ]

        log.info("Probing /api/v1/history for working interval format ...")
        for iv, sk, ek in candidates:
            payload = {"symbol": symbol, "exchange": exchange,
                       "interval": iv, sk: start, ek: end}
            # Use raw post so we can see the actual response for debugging
            payload["apikey"] = self.api_key
            url = f"{self.host}/api/v1/history"
            try:
                r = self.session.post(url, json=payload, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("status") == "success" and data.get("data"):
                        log.info("  ✓  interval=%r  %s/  %s  → HTTP 200, %d rows",
                                 iv, sk, ek, len(data["data"]))
                        OpenAlgoClient._hist_interval  = iv
                        OpenAlgoClient._hist_start_key = sk
                        OpenAlgoClient._hist_end_key   = ek
                        return True
                    else:
                        log.debug("  ✗  interval=%r → 200 but status=%s msg=%s",
                                  iv, data.get("status"), data.get("message",""))
                else:
                    log.debug("  ✗  interval=%r → HTTP %d", iv, r.status_code)
            except Exception as e:
                log.debug("  ✗  interval=%r → %s", iv, e)

        log.error("No working /api/v1/history format found for %s/%s.", symbol, exchange)
        log.error("Check: is OpenAlgo connected to your broker and are master contracts downloaded?")
        return False

    # ── History ───────────────────────────────────────────────────────
    def get_history(self, symbol: str, exchange: str,
                    start: str, end: str,
                    interval: str = "") -> Optional[pd.DataFrame]:
        iv = interval or OpenAlgoClient._hist_interval or "D"
        sk = OpenAlgoClient._hist_start_key or "start_date"
        ek = OpenAlgoClient._hist_end_key   or "end_date"

        data = self._post("/api/v1/history", {
            "symbol": symbol, "exchange": exchange,
            "interval": iv, sk: start, ek: end,
        }, silent=True)
        if data.get("status") != "success":
            return None

        raw = data.get("data", [])
        if not raw:
            return None

        df = pd.DataFrame(raw)
        df.columns = [c.lower() for c in df.columns]

        rename = {
            "timestamp": "date", "time": "date", "datetime": "date",
            "vol": "volume", "o": "open", "h": "high",
            "l": "low", "c": "close", "v": "volume",
        }
        df.rename(columns={k: v for k, v in rename.items() if k in df.columns},
                  inplace=True)

        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            return None

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df.sort_values("date", inplace=True)

        df.dropna(subset=list(required), inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    # ── Live quotes (NFO) ─────────────────────────────────────────────
    def get_ltp(self, symbol: str, exchange: str) -> Optional[float]:
        data = self._post("/api/v1/quotes",
                          {"symbol": symbol, "exchange": exchange}, silent=True)
        if data.get("status") == "success":
            ltp = data.get("data", {}).get("ltp")
            return float(ltp) if ltp else None
        return None

    @staticmethod
    def short_expiry(expiry: str) -> str:
        """'28JUL2026' → '28JUL26'"""
        e = expiry.upper().strip()
        return e[:5] + e[7:] if len(e) == 9 else e

    def get_futures_ltp(self, symbol: str, short_exp: str) -> Optional[float]:
        return self.get_ltp(f"{symbol}{short_exp}FUT", "NFO")

    def get_option_ltp(self, symbol: str, short_exp: str,
                       strike: float, opt: str) -> Optional[float]:
        s = str(int(strike)) if strike == int(strike) else f"{strike:.1f}"
        return self.get_ltp(f"{symbol}{short_exp}{s}{opt.upper()}", "NFO")


# ══════════════════════════════════════════════════════════════════════
#  PASS 1 + 2 — ADX & CANDLE INDICATORS  (pure computation on OHLCV)
# ══════════════════════════════════════════════════════════════════════

def _wilder(series: pd.Series, period: int) -> pd.Series:
    result = np.full(len(series), np.nan)
    vals   = series.values
    # find first index with enough non-nan values
    start  = period - 1
    while start < len(vals) and np.isnan(vals[start - period + 1:start + 1]).any():
        start += 1
    if start >= len(vals):
        return pd.Series(result, index=series.index)

    seed = np.nanmean(vals[start - period + 1: start + 1])
    result[start] = seed
    prev = seed
    for i in range(start + 1, len(vals)):
        if np.isnan(vals[i]):
            result[i] = np.nan
        else:
            prev = (prev * (period - 1) + vals[i]) / period
            result[i] = prev
    return pd.Series(result, index=series.index)


def compute_indicators(df: pd.DataFrame, adx_period: int, avg_period: int) -> pd.DataFrame:
    """Compute ALL indicators in one pass over the OHLCV DataFrame."""
    df = df.copy()

    high  = df["high"]
    low   = df["low"]
    close = df["close"]
    open_ = df["open"]

    # ── True Range & Directional Movement ────────────────────────────
    prev_c = close.shift(1)
    tr = pd.concat([high - low,
                    (high - prev_c).abs(),
                    (low  - prev_c).abs()], axis=1).max(axis=1)

    up   = high - high.shift(1)
    down = low.shift(1) - low
    pdm  = pd.Series(np.where((up > down) & (up > 0),   up,   0.0), index=df.index)
    mdm  = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)

    atr_s  = _wilder(tr,  adx_period)
    pdm_s  = _wilder(pdm, adx_period)
    mdm_s  = _wilder(mdm, adx_period)

    plus_di  = 100 * pdm_s  / atr_s.replace(0, np.nan)
    minus_di = 100 * mdm_s  / atr_s.replace(0, np.nan)
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx      = _wilder(dx, adx_period)

    df["plus_di"]  = plus_di
    df["minus_di"] = minus_di
    df["adx"]      = adx

    # ── Candle indicators ─────────────────────────────────────────────
    body  = close - open_
    range_ = high - low

    avg_body = body.abs().rolling(avg_period).mean()
    avg_vol  = df["volume"].rolling(avg_period).mean()

    df["candle_type"]     = np.where(body < 0, "Supply", "Demand")
    df["candle_strength"] = (body.abs() / range_.replace(0, np.nan) * 100).round(2)
    df["candle_size"]     = (body.abs() / avg_body.replace(0, np.nan) * 100).round(2)
    df["volume_pct"]      = ((df["volume"] - avg_vol) / avg_vol.replace(0, np.nan) * 100).round(2)
    df["comment"]         = np.where(
        (df["candle_type"] == "Supply") & (df["volume_pct"] < 0), "Inaction",
        np.where(
            (df["candle_type"] == "Supply") & (df["volume_pct"] >= 0), "Action",
            np.where(df["volume_pct"] < 0, "Quiet Demand", "Active Demand"),
        ),
    )
    return df


def adx_trend_label(adx_series: pd.Series, window: int) -> str:
    vals  = adx_series.dropna().iloc[-window:]
    if len(vals) < 2:
        return "Neutral"
    diffs = vals.diff().dropna()
    if (diffs > 0).all():   return "Trending"
    if (diffs < 0).all():   return "Weakening"
    return "Neutral"


def three_candle_pattern(df: pd.DataFrame) -> str:
    if len(df) < 3:
        return "—"
    last3 = df.iloc[-3:]
    types    = list(last3["candle_type"])
    comments = list(last3["comment"])
    cur = comments[-1]
    if cur == "Inaction":
        return "Inaction after Demand" if "Demand" in types[:-1] else "Consecutive Inaction"
    return f"{types[-1]} / {cur}"


# ══════════════════════════════════════════════════════════════════════
#  PASS 3 — STRADDLE IDENTIFICATION  (live NFO quotes)
# ══════════════════════════════════════════════════════════════════════

def snap(price: float, step: float) -> float:
    return round(price / step) * step


def fetch_straddle(client: OpenAlgoClient, symbol: str,
                   step: float, short_exp: str) -> dict:
    """Fetch futures + ATM CE/PE and compute parity metrics."""
    empty = {
        "fut_ltp": None, "atm": None,
        "ce_ltp": None, "pe_ltp": None,
        "spread_pct": None, "straddle_prem": None,
        "straddle_val": None, "straddle_pass": False,
        "otm_ce": None, "otm_pe": None,
        "strangle_prem": None, "strangle_val": None,
    }

    fut = client.get_futures_ltp(symbol, short_exp)
    if not fut:
        return empty

    atm   = snap(fut, step)
    ce    = client.get_option_ltp(symbol, short_exp, atm, "CE")
    pe    = client.get_option_ltp(symbol, short_exp, atm, "PE")

    if not ce or not pe:
        return {**empty, "fut_ltp": fut, "atm": atm}

    mid        = (ce + pe) / 2
    spread_pct = abs(ce - pe) / mid * 100 if mid > 0 else 999

    otm_ce = client.get_option_ltp(symbol, short_exp, atm + step, "CE")
    otm_pe = client.get_option_ltp(symbol, short_exp, atm - step, "PE")

    straddle_prem  = ce + pe
    strangle_prem  = (otm_ce or 0) + (otm_pe or 0)

    return {
        "fut_ltp":      fut,
        "atm":          atm,
        "ce_ltp":       ce,
        "pe_ltp":       pe,
        "spread_pct":   round(spread_pct, 2),
        "straddle_prem": round(straddle_prem, 2),
        "straddle_val": None,   # filled in after lot size is known
        "straddle_pass": spread_pct <= Config.ATM_PARITY_PCT,
        "otm_ce":       otm_ce,
        "otm_pe":       otm_pe,
        "strangle_prem": round(strangle_prem, 2),
        "strangle_val": None,
    }


# ══════════════════════════════════════════════════════════════════════
#  FULL SYMBOL PIPELINE
# ══════════════════════════════════════════════════════════════════════

def process_symbol(client: OpenAlgoClient, symbol: str,
                   lot: int, step: float,
                   start_str: str, end_str: str,
                   short_exp: str, config: Config) -> Optional[dict]:
    # ── Pass 1 + 2: history → indicators ─────────────────────────────
    df = client.get_history(symbol, config.NSE_EXCHANGE, start_str, end_str)
    if df is None or len(df) < config.ADX_PERIOD + config.AVG_PERIOD:
        log.warning("    %-14s  insufficient history (%s rows)",
                    symbol, len(df) if df is not None else 0)
        return None

    df  = compute_indicators(df, config.ADX_PERIOD, config.AVG_PERIOD)
    last = df.iloc[-1]
    prev = df.iloc[-2]

    adx_now = last["adx"]
    adx_pre = prev["adx"]
    if pd.isna(adx_now) or pd.isna(adx_pre):
        return None

    adx_chg   = (adx_now - adx_pre) / adx_pre * 100
    adx_trend = adx_trend_label(df["adx"], config.ADX_TREND_WINDOW)
    plus_di   = last["plus_di"]
    minus_di  = last["minus_di"]
    dmi_trend = "Positive" if plus_di > minus_di else "Negative"
    dmi_diff  = abs(plus_di - minus_di) / (minus_di if minus_di > 0 else 1) * 100

    adx_pass = (adx_chg < 0) and (adx_trend == "Neutral")

    candle_type  = last["candle_type"]
    c_strength   = last["candle_strength"]
    c_size       = last["candle_size"]
    vol_pct      = last["volume_pct"]
    comment      = last["comment"]
    pattern      = three_candle_pattern(df)

    candle_pass  = (comment == "Inaction")

    lcp     = round(last["close"], 2)
    pct_chg = round((last["close"] - prev["close"]) / prev["close"] * 100, 2)

    # ── Pass 3: live straddle quotes ──────────────────────────────────
    st = fetch_straddle(client, symbol, step, short_exp)
    if st["straddle_val"] is None and st["straddle_prem"]:
        st["straddle_val"]  = round(st["straddle_prem"]  * lot, 0)
    if st["strangle_val"] is None and st["strangle_prem"]:
        st["strangle_val"]  = round(st["strangle_prem"]  * lot, 0)

    straddle_pass = st["straddle_pass"]

    # ── Signal tier ───────────────────────────────────────────────────
    n = sum([adx_pass, candle_pass, straddle_pass])
    if n == 3:   tier = "★★★ PRIME"
    elif n == 2: tier = "★★  READY"
    elif n == 1: tier = "★   WATCH"
    else:        tier = "—"

    return {
        "symbol":        symbol,
        "lot":           lot,
        "step":          step,
        "lcp":           lcp,
        "pct_chg":       pct_chg,
        # ADX
        "adx":           round(adx_now, 2),
        "plus_di":       round(plus_di, 2),
        "minus_di":      round(minus_di, 2),
        "adx_chg_pct":   round(adx_chg, 2),
        "dmi_diff_pct":  round(dmi_diff, 2),
        "dmi_trend":     dmi_trend,
        "adx_trend":     adx_trend,
        "adx_pass":      adx_pass,
        # Candle
        "candle_type":   candle_type,
        "c_strength":    c_strength,
        "c_size":        c_size,
        "vol_pct":       vol_pct,
        "comment":       comment,
        "pattern":       pattern,
        "candle_pass":   candle_pass,
        # Straddle
        "fut_ltp":       st["fut_ltp"],
        "atm":           st["atm"],
        "ce_ltp":        st["ce_ltp"],
        "pe_ltp":        st["pe_ltp"],
        "spread_pct":    st["spread_pct"],
        "straddle_prem": st["straddle_prem"],
        "straddle_val":  st["straddle_val"],
        "strangle_prem": st["strangle_prem"],
        "strangle_val":  st["strangle_val"],
        "straddle_pass": straddle_pass,
        # Combined
        "tier":          tier,
        "n_pass":        n,
    }


# ══════════════════════════════════════════════════════════════════════
#  DISPLAY
# ══════════════════════════════════════════════════════════════════════

TIER_COLOR = {
    "★★★ PRIME": Fore.GREEN,
    "★★  READY": Fore.YELLOW,
    "★   WATCH": Fore.CYAN,
    "—":         "",
}

def _c(val, color): return f"{color}{val}{Style.RESET_ALL}" if color else str(val)
def _na(val, fmt="{}"): return fmt.format(val) if val is not None else "  —  "


def print_banner(config: Config):
    print(f"""
{Fore.CYAN}╔═══════════════════════════════════════════════════════════════════════════╗
║   UNIFIED STRADDLE SCREENER  —  OpenAlgo  |  NSE + NFO             ║
║   Expiry : {config.TARGET_EXPIRY:<12} |  ATM Parity : ≤{config.ATM_PARITY_PCT:.1f}%              ║
║   ADX    : Neutral trend + falling  |  Candle : Supply + low vol    ║
║   Signal : ★★★ PRIME = all 3 pass  |  ★★ READY = ADX+Candle       ║
╚═══════════════════════════════════════════════════════════════════════════╝{Style.RESET_ALL}""")


def print_results(results: list, config: Config, run_num: int):
    if not results:
        print(f"\n{Fore.RED}  No results.{Style.RESET_ALL}\n")
        return

    results.sort(key=lambda r: (-r["n_pass"], r["adx"]))

    ts      = datetime.now().strftime("%H:%M:%S")
    n_prime = sum(1 for r in results if r["tier"] == "★★★ PRIME")
    n_ready = sum(1 for r in results if r["tier"] == "★★  READY")
    n_watch = sum(1 for r in results if r["tier"] == "★   WATCH")

    print(f"\n  Run #{run_num}  [{ts}]  {len(results)} symbols  |  "
          f"{Fore.GREEN}★★★ {n_prime} PRIME{Style.RESET_ALL}  |  "
          f"{Fore.YELLOW}★★ {n_ready} READY{Style.RESET_ALL}  |  "
          f"{Fore.CYAN}★ {n_watch} WATCH{Style.RESET_ALL}\n")

    # ── SECTION 1: ADX table ──────────────────────────────────────────
    print(f"{Fore.WHITE}{'─'*10}  PASS 1  ADX SCREENER  {'─'*50}{Style.RESET_ALL}")
    adx_headers = ["#", "Symbol", "LCP", "Chg%", "ADX", "+DI", "-DI",
                   "ADXChg%", "DMI Diff", "DMI Trend", "ADX Trend", "ADX✓"]
    adx_rows = []
    for i, r in enumerate(results, 1):
        ac = Fore.GREEN if r["adx"] < 20 else (Fore.YELLOW if r["adx"] < 25 else Fore.RED)
        dt = Fore.GREEN if r["dmi_trend"] == "Positive" else Fore.RED
        at = Fore.YELLOW if r["adx_trend"] == "Neutral" else Fore.WHITE
        adx_rows.append([
            i,
            _c(f"{r['symbol']:<12}", Fore.CYAN),
            f"{r['lcp']:>9,.2f}",
            f"{r['pct_chg']:>+6.2f}%",
            _c(f"{r['adx']:>6.2f}", ac),
            f"{r['plus_di']:>5.2f}",
            f"{r['minus_di']:>5.2f}",
            f"{r['adx_chg_pct']:>+7.2f}%",
            f"{r['dmi_diff_pct']:>7.2f}%",
            _c(f"{r['dmi_trend']:<8}", dt),
            _c(f"{r['adx_trend']:<10}", at),
            _c("✓", Fore.GREEN) if r["adx_pass"] else "✗",
        ])
    print(tabulate(adx_rows, headers=adx_headers, tablefmt="rounded_outline"))

    # ── SECTION 2: Candle-Volume table ────────────────────────────────
    print(f"\n{Fore.WHITE}{'─'*10}  PASS 2  CANDLE-VOLUME SCREENER  {'─'*38}{Style.RESET_ALL}")
    cndl_headers = ["#", "Symbol", "LCP", "Candle Type", "Strength", "Candle Size",
                    "Vol%", "Comment", "3-Candle Pattern", "Candle✓"]
    cndl_rows = []
    for i, r in enumerate(results, 1):
        vc  = Fore.GREEN if (r["vol_pct"] or 0) < 0 else Fore.RED
        cc  = Fore.GREEN if r["candle_pass"] else ""
        cndl_rows.append([
            i,
            _c(f"{r['symbol']:<12}", Fore.CYAN),
            f"{r['lcp']:>9,.2f}",
            _c(f"{r['candle_type']:<7}", Fore.RED if r["candle_type"] == "Supply" else Fore.GREEN),
            _na(r["c_strength"], "{:>7.2f}%"),
            _na(r["c_size"],     "{:>9.2f}%"),
            _c(_na(r["vol_pct"], "{:>+7.2f}%"), vc),
            _c(f"{r['comment']:<16}", cc),
            f"{r['pattern']:<28}",
            _c("✓", Fore.GREEN) if r["candle_pass"] else "✗",
        ])
    print(tabulate(cndl_rows, headers=cndl_headers, tablefmt="rounded_outline"))

    # ── SECTION 3: Straddle table ─────────────────────────────────────
    print(f"\n{Fore.WHITE}{'─'*10}  PASS 3  STRADDLE IDENTIFICATION  {'─'*38}{Style.RESET_ALL}")
    str_headers = ["#", "Symbol", "Lot", "Fut LTP", "ATM Strike",
                   "ATM CE", "ATM PE", "Spread%",
                   "Straddle Prem", "Straddle Value",
                   "Strangle Prem", "Strangle Value", "Parity✓"]
    str_rows = []
    for i, r in enumerate(results, 1):
        sp_c = Fore.GREEN if r["straddle_pass"] else (
               Fore.YELLOW if (r["spread_pct"] or 99) < 5 else Fore.RED)
        str_rows.append([
            i,
            _c(f"{r['symbol']:<12}", Fore.CYAN),
            f"{r['lot']:>6,}",
            _na(r["fut_ltp"],       "{:>10,.2f}"),
            _na(r["atm"],           "{:>10,.1f}"),
            _na(r["ce_ltp"],        "{:>8,.2f}"),
            _na(r["pe_ltp"],        "{:>8,.2f}"),
            _c(_na(r["spread_pct"], "{:>6.2f}%"), sp_c),
            _na(r["straddle_prem"], "{:>12,.2f}"),
            _na(r["straddle_val"],  "{:>13,.0f}"),
            _na(r["strangle_prem"], "{:>12,.2f}"),
            _na(r["strangle_val"],  "{:>13,.0f}"),
            _c("✓", Fore.GREEN) if r["straddle_pass"] else "✗",
        ])
    print(tabulate(str_rows, headers=str_headers, tablefmt="rounded_outline"))

    # ── SECTION 4: Combined signal summary ────────────────────────────
    print(f"\n{Fore.WHITE}{'─'*10}  COMBINED SIGNAL SUMMARY  {'─'*46}{Style.RESET_ALL}")
    sig_headers = ["#", "Symbol", "LCP", "Lot",
                   "ADX✓", "Candle✓", "Parity✓", "Signal"]
    sig_rows = []
    for i, r in enumerate(results, 1):
        tc = TIER_COLOR.get(r["tier"], "")
        sig_rows.append([
            i,
            _c(f"{r['symbol']:<12}", Fore.CYAN),
            f"{r['lcp']:>9,.2f}",
            f"{r['lot']:>6,}",
            _c("✓", Fore.GREEN) if r["adx_pass"]      else "✗",
            _c("✓", Fore.GREEN) if r["candle_pass"]    else "✗",
            _c("✓", Fore.GREEN) if r["straddle_pass"]  else "✗",
            _c(r["tier"], tc),
        ])
    print(tabulate(sig_rows, headers=sig_headers, tablefmt="rounded_outline"))

    # ── PRIME detail block ────────────────────────────────────────────
    prime = [r for r in results if r["tier"] == "★★★ PRIME"]
    if prime:
        print(f"\n{Fore.GREEN}  ★★★  PRIME STRADDLE ENTRIES  (all 3 filters pass){Style.RESET_ALL}")
        print(f"  {'Symbol':<14} {'ATM':>8} {'CE':>8} {'PE':>8} "
              f"{'Spread':>8} {'StraddlePrem':>14} {'Lot':>6} {'Credit':>10}")
        print("  " + "─" * 82)
        for r in prime:
            print(
                f"  {Fore.CYAN}{r['symbol']:<14}{Style.RESET_ALL}"
                f" {_na(r['atm'],'{:>8,.1f}')}"
                f" {_na(r['ce_ltp'],'{:>8,.2f}')}"
                f" {_na(r['pe_ltp'],'{:>8,.2f}')}"
                f" {Fore.GREEN}{_na(r['spread_pct'],'{:>7.2f}%')}{Style.RESET_ALL}"
                f" {_na(r['straddle_prem'],'{:>13,.2f}')}"
                f" {r['lot']:>6,}"
                f" {Fore.GREEN}{_na(r['straddle_val'],'{:>10,.0f}')}{Style.RESET_ALL}"
            )
        print(f"\n  Action: Sell 1 lot ATM CE + 1 lot ATM PE for each PRIME symbol.")
        print(f"  Strangle alt: Sell OTM CE (ATM+step) + OTM PE (ATM-step) for lower margin.\n")

    ready = [r for r in results if r["tier"] == "★★  READY"]
    if ready:
        print(f"{Fore.YELLOW}  ★★  READY  (ADX + Candle pass — watch for parity tightening){Style.RESET_ALL}")
        print(f"  " + "  ".join(r["symbol"] for r in ready) + "\n")

    print(f"  Legend: ★★★ PRIME = all 3 | ★★ READY = ADX+Candle | ★ WATCH = 1 filter\n")


# ══════════════════════════════════════════════════════════════════════
#  MAIN RUNNER
# ══════════════════════════════════════════════════════════════════════

_run_count = 0

def run_once(client: OpenAlgoClient, config: Config):
    global _run_count
    _run_count += 1

    short_exp = OpenAlgoClient.short_expiry(config.TARGET_EXPIRY)
    end_dt    = datetime.today()
    start_dt  = end_dt - timedelta(days=config.HISTORY_DAYS)
    end_str   = end_dt.strftime("%Y-%m-%d")
    start_str = start_dt.strftime("%Y-%m-%d")

    log.info("Run #%d — fetching history for %d symbols (parallel, %d workers) ...",
             _run_count, len(config.WATCHLIST), config.MAX_WORKERS)

    results = []

    # Parallel history fetch + compute; serial live quote fetch to avoid rate limits
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as ex:
        futures = {
            ex.submit(process_symbol, client, sym, lot, step,
                      start_str, end_str, short_exp, config): sym
            for sym, lot, step in config.WATCHLIST
        }
        for f in as_completed(futures):
            sym = futures[f]
            try:
                r = f.result()
                if r:
                    results.append(r)
            except Exception as e:
                log.error("Error processing %s: %s", sym, e)

    print_results(results, config, _run_count)

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = f"unified_{ts}.json"
    with open(out_file, "w") as fh:
        json.dump(results, fh, indent=2, default=str)
    log.info("Results saved → %s", out_file)


def main():
    config = Config()
    client = OpenAlgoClient(config.OPENALGO_HOST, config.API_KEY)

    print_banner(config)

    # ── Auto-detect working history API format (runs once) ────────────
    if not client.probe_history_api():
        log.error("Aborting — cannot fetch historical data from OpenAlgo.")
        log.error("Steps to fix:")
        log.error("  1. Open http://127.0.0.1:5000 in your browser")
        log.error("  2. Confirm your broker shows 'Connected'")
        log.error("  3. Go to Settings → Master Contracts → click Download/Sync")
        log.error("  4. Wait for download to complete, then re-run this script")
        return

    if config.RUN_INTERVAL_MIN <= 0:
        run_once(client, config)
        return

    import schedule
    interval = config.RUN_INTERVAL_MIN
    log.info("Auto-scan every %d minutes. Ctrl+C to stop.", interval)

    run_once(client, config)   # immediate first run
    schedule.every(interval).minutes.do(run_once, client=client, config=config)

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
