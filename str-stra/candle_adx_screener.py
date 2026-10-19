"""
═══════════════════════════════════════════════════════════════════════
 CANDLE-VOLUME + ADX SCREENER  —  OpenAlgo Edition
 Author : for VS | Date: July 2026
═══════════════════════════════════════════════════════════════════════

 Combines two pre-filters for straddle/strangle candidate selection:

 FILTER 1 — ADX Digger logic
   • ADX is falling (ADX_Change < 0)
   • ADX Trend = "Neutral" (not a clean trending or weakening state)
   • Optional sub-filter: ADX < 25 for lowest-energy stocks

 FILTER 2 — Candle Volume "Inaction" logic
   • Candle Type = Supply (close < open, bearish body)
   • Volume % < 0 (below 20-day average volume)
   • → Weak selling = no directional commitment = range likely

 COMBINED SIGNAL:
   Stock appears on BOTH filters → highest-conviction straddle/strangle

 Data source:
   OpenAlgo /api/v1/history  (daily OHLCV, NSE)
   All indicators computed in Python — no external data feed needed.

 Requirements:
   pip install requests pandas numpy tabulate colorama

 Run:  python candle_adx_screener.py
═══════════════════════════════════════════════════════════════════════
"""

import os
import json
import logging
import warnings
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
log = logging.getLogger("CandleADX")


# ══════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

class Config:
    OPENALGO_HOST = "http://127.0.0.1:5000"
    API_KEY       = os.getenv("OPENALGO_API_KEY", "5f70f56afb6677db1d95aaa85a2e639c71b7b61f32555307d3bd4fe15c83445f")
    EXCHANGE      = "NSE"

    # How many calendar days of history to fetch (need ~60 trading days for ADX-14)
    HISTORY_DAYS  = 90

    # ── ADX parameters ────────────────────────────────────────────────
    ADX_PERIOD    = 14       # standard ADX smoothing period
    ADX_MAX       = 25.0     # flag stocks with ADX below this (low directional energy)

    # ── Candle / volume parameters ────────────────────────────────────
    AVG_PERIOD    = 20       # lookback for volume and body-size averages
    CANDLE_BARS   = 3        # three-candle pattern window (how many recent bars to check)

    # ── Combined-signal output ────────────────────────────────────────
    # "both"  → only stocks passing BOTH ADX and Candle filters
    # "any"   → stocks passing EITHER filter
    SIGNAL_MODE   = "both"

    # Watchlist: (Symbol, LotSize, StrikeStep) — same as hedge/str-stra screeners
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
#  OPENALGO HISTORY CLIENT
# ══════════════════════════════════════════════════════════════════════

class OpenAlgoClient:

    def __init__(self, host: str, api_key: str):
        self.host    = host.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _post(self, endpoint: str, payload: dict, silent: bool = False) -> dict:
        payload["apikey"] = self.api_key
        url = f"{self.host}{endpoint}"
        try:
            resp = self.session.post(url, json=payload, timeout=15)
            if resp.status_code == 400 and silent:
                return {}
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError:
            if not silent:
                log.error("Cannot reach OpenAlgo at %s — is the server running?", self.host)
            return {}
        except Exception as e:
            if not silent:
                log.error("API error [%s]: %s", endpoint, e)
            return {}

    def get_history(self, symbol: str, exchange: str,
                    start_date: str, end_date: str,
                    interval: str = "1d") -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV history from OpenAlgo.
        Returns a DataFrame with columns: date, open, high, low, close, volume
        sorted oldest → newest.
        """
        data = self._post("/api/v1/history", {
            "symbol":     symbol,
            "exchange":   exchange,
            "interval":   interval,
            "start_date": start_date,
            "end_date":   end_date,
        })

        if data.get("status") != "success":
            log.warning("    No history for %s (%s)", symbol, data.get("message", "unknown"))
            return None

        raw = data.get("data", [])
        if not raw:
            log.warning("    Empty history for %s", symbol)
            return None

        df = pd.DataFrame(raw)

        # Normalise column names (OpenAlgo may return different casings)
        df.columns = [c.lower() for c in df.columns]

        # Map common alternative names
        rename = {
            "timestamp": "date", "time": "date", "datetime": "date",
            "vol": "volume", "o": "open", "h": "high", "l": "low",
            "c": "close", "v": "volume",
        }
        df.rename(columns={k: v for k, v in rename.items() if k in df.columns}, inplace=True)

        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            log.warning("    History missing columns for %s: got %s", symbol, list(df.columns))
            return None

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df.sort_values("date", inplace=True)

        df.dropna(subset=["open", "high", "low", "close", "volume"], inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df


# ══════════════════════════════════════════════════════════════════════
#  TECHNICAL INDICATORS
# ══════════════════════════════════════════════════════════════════════

def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (used in ATR and ADX)."""
    result = [np.nan] * len(series)
    # seed with simple average of first `period` values
    valid = series.dropna()
    if len(valid) < period:
        return pd.Series(result, index=series.index)

    first_idx = series.first_valid_index()
    iloc_start = series.index.get_loc(first_idx)

    seed_slice = series.iloc[iloc_start: iloc_start + period]
    if seed_slice.isna().any():
        return pd.Series(result, index=series.index)

    prev = seed_slice.mean()
    result[iloc_start + period - 1] = prev

    for i in range(iloc_start + period, len(series)):
        val = series.iloc[i]
        if pd.isna(val):
            result[i] = np.nan
        else:
            prev = (prev * (period - 1) + val) / period
            result[i] = prev

    return pd.Series(result, index=series.index)


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Compute ADX, +DI, -DI using Wilder's smoothing.
    Appends columns: tr, plus_dm, minus_dm, atr, plus_di, minus_di, dx, adx
    """
    high  = df["high"]
    low   = df["low"]
    close = df["close"]

    prev_close = close.shift(1)
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)

    # True Range
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Directional Movement
    up_move   = high - prev_high
    down_move = prev_low - low

    plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move,   0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_dm  = pd.Series(plus_dm,  index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    # Wilder smooth
    atr_s      = wilder_smooth(tr,       period)
    plus_dm_s  = wilder_smooth(plus_dm,  period)
    minus_dm_s = wilder_smooth(minus_dm, period)

    plus_di  = 100 * plus_dm_s  / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm_s / atr_s.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = wilder_smooth(dx, period)

    df = df.copy()
    df["tr"]       = tr
    df["plus_di"]  = plus_di
    df["minus_di"] = minus_di
    df["dx"]       = dx
    df["adx"]      = adx
    return df


def classify_adx_trend(adx_series: pd.Series, window: int = 3) -> str:
    """
    Replicate Zoneweb's ADX Trend classification.
    Uses the last `window` ADX values to determine direction.
      Rising consistently  → "Trending"
      Falling consistently → "Weakening"
      Mixed / flat         → "Neutral"
    """
    vals = adx_series.dropna().iloc[-window:]
    if len(vals) < 2:
        return "Neutral"
    diffs = vals.diff().dropna()
    if (diffs > 0).all():
        return "Trending"
    if (diffs < 0).all():
        return "Weakening"
    return "Neutral"


def compute_candle_indicators(df: pd.DataFrame, avg_period: int = 20) -> pd.DataFrame:
    """
    Compute candle-volume indicators on the full DataFrame.
    Appends: body, candle_type, candle_strength, candle_size, volume_pct, comment
    """
    df = df.copy()
    df["body"]   = df["close"] - df["open"]      # positive = bullish, negative = bearish
    df["range"]  = df["high"] - df["low"]

    df["candle_type"] = np.where(df["body"] < 0, "Supply", "Demand")

    # Candle Strength: |body| / range × 100
    df["candle_strength"] = (df["body"].abs() / df["range"].replace(0, np.nan) * 100).round(2)

    # Candle Size: |body| relative to avg |body| over lookback
    avg_body = df["body"].abs().rolling(avg_period).mean()
    df["candle_size"] = (df["body"].abs() / avg_body.replace(0, np.nan) * 100).round(2)

    # Volume %: vs avg volume over lookback
    avg_vol = df["volume"].rolling(avg_period).mean()
    df["volume_pct"] = ((df["volume"] - avg_vol) / avg_vol.replace(0, np.nan) * 100).round(2)

    # Inaction: Supply candle + below-average volume
    df["comment"] = np.where(
        (df["candle_type"] == "Supply") & (df["volume_pct"] < 0),
        "Inaction",
        np.where(
            (df["candle_type"] == "Supply") & (df["volume_pct"] >= 0),
            "Action (Supply)",
            np.where(
                (df["candle_type"] == "Demand") & (df["volume_pct"] < 0),
                "Quiet Demand",
                "Active Demand",
            ),
        ),
    )
    return df


# ══════════════════════════════════════════════════════════════════════
#  THREE-CANDLE PATTERN CHECK
# ══════════════════════════════════════════════════════════════════════

def check_three_candle_pattern(df: pd.DataFrame) -> dict:
    """
    Evaluate the last 3 candles as a group.
    Returns pattern summary for the most recent bar.
    """
    if len(df) < 3:
        return {"pattern": "Insufficient data", "bars": []}

    last3 = df.iloc[-3:].copy()
    bars  = []
    for _, row in last3.iterrows():
        bars.append({
            "date":    str(row.get("date", ""))[:10],
            "type":    row["candle_type"],
            "comment": row["comment"],
            "vol_pct": row["volume_pct"],
        })

    # Simple pattern: last bar is Inaction after prior bars were not
    current   = bars[-1]
    prior_two = bars[:-1]

    if current["comment"] == "Inaction":
        prior_types = [b["type"] for b in prior_two]
        if "Demand" in prior_types:
            pattern = "Inaction after Demand"   # supply after buying → stall
        else:
            pattern = "Consecutive Inaction"    # sustained range
    else:
        pattern = f"{current['type']} — {current['comment']}"

    return {"pattern": pattern, "current": current, "bars": bars}


# ══════════════════════════════════════════════════════════════════════
#  PER-SYMBOL ANALYSIS
# ══════════════════════════════════════════════════════════════════════

def analyse_symbol(
    client:  OpenAlgoClient,
    symbol:  str,
    lot:     int,
    step:    float,
    config:  Config,
) -> Optional[dict]:
    log.info("  Analysing %-14s  lot=%-5d", symbol, lot)

    end_dt   = datetime.today()
    start_dt = end_dt - timedelta(days=config.HISTORY_DAYS)
    end_str  = end_dt.strftime("%Y-%m-%d")
    start_str = start_dt.strftime("%Y-%m-%d")

    df = client.get_history(symbol, config.EXCHANGE, start_str, end_str)
    if df is None or len(df) < config.ADX_PERIOD + 5:
        log.warning("    Not enough history for %s (%s rows)", symbol, len(df) if df is not None else 0)
        return None

    # ── Compute indicators ────────────────────────────────────────────
    df = compute_adx(df, config.ADX_PERIOD)
    df = compute_candle_indicators(df, config.AVG_PERIOD)

    # ── ADX metrics (last bar) ────────────────────────────────────────
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last

    adx_now    = last["adx"]
    adx_prev   = prev["adx"]

    if pd.isna(adx_now) or pd.isna(adx_prev):
        log.warning("    ADX not computable for %s (too few bars?)", symbol)
        return None

    adx_change = (adx_now - adx_prev) / adx_prev * 100   # % change, 1-bar
    adx_trend  = classify_adx_trend(df["adx"], window=3)
    plus_di    = last["plus_di"]
    minus_di   = last["minus_di"]

    dmi_trend  = "Positive" if plus_di > minus_di else "Negative"
    dmi_diff   = abs(plus_di - minus_di) / (minus_di if minus_di > 0 else 1) * 100

    # ── Candle metrics (last bar) ─────────────────────────────────────
    candle_type     = last["candle_type"]
    candle_strength = last["candle_strength"]
    candle_size     = last["candle_size"]
    volume_pct      = last["volume_pct"]
    comment         = last["comment"]

    three_candle = check_three_candle_pattern(df)

    # ── Filter signals ────────────────────────────────────────────────
    # ADX filter: falling ADX + Neutral trend
    adx_pass = (adx_change < 0) and (adx_trend == "Neutral")

    # Candle filter: Inaction (Supply + low volume)
    candle_pass = (comment == "Inaction")

    # Combined
    both_pass  = adx_pass and candle_pass
    either_pass = adx_pass or candle_pass

    return {
        "symbol":           symbol,
        "lot":              lot,
        "step":             step,
        "lcp":              round(last["close"], 2),
        "pct_change":       round((last["close"] - prev["close"]) / prev["close"] * 100, 2),

        # ADX
        "adx":              round(adx_now, 2),
        "plus_di":          round(plus_di, 2),
        "minus_di":         round(minus_di, 2),
        "adx_change_pct":   round(adx_change, 2),
        "dmi_diff_pct":     round(dmi_diff, 2),
        "dmi_trend":        dmi_trend,
        "adx_trend":        adx_trend,
        "adx_pass":         adx_pass,

        # Candle
        "candle_type":      candle_type,
        "candle_strength":  candle_strength,
        "candle_size":      candle_size,
        "volume_pct":       volume_pct,
        "comment":          comment,
        "three_candle":     three_candle["pattern"],
        "candle_pass":      candle_pass,

        # Signal
        "both_pass":        both_pass,
        "either_pass":      either_pass,
    }


# ══════════════════════════════════════════════════════════════════════
#  DISPLAY
# ══════════════════════════════════════════════════════════════════════

def _adx_color(adx: float) -> str:
    if adx < 20:   return Fore.GREEN
    if adx < 25:   return Fore.YELLOW
    return Fore.RED

def _vol_color(vol_pct: float) -> str:
    return Fore.GREEN if vol_pct < 0 else Fore.RED

def _pass_flag(r: dict) -> str:
    if r["both_pass"]:   return f"{Fore.GREEN}★ BOTH{Style.RESET_ALL}"
    if r["adx_pass"]:    return f"{Fore.YELLOW}◆ ADX {Style.RESET_ALL}"
    if r["candle_pass"]: return f"{Fore.CYAN}● CNDL{Style.RESET_ALL}"
    return ""


def print_banner(config: Config):
    print(f"""
{Fore.CYAN}╔══════════════════════════════════════════════════════════════════════╗
║   CANDLE-VOLUME + ADX SCREENER  —  OpenAlgo (NSE Daily)           ║
║   ADX filter  : Neutral trend + ADX falling  (ADX < {config.ADX_MAX:.0f} preferred) ║
║   Candle filter: Supply candle + Volume below 20-day average       ║
║   Combined ★  : both filters pass → highest-conviction candidates  ║
╚══════════════════════════════════════════════════════════════════════╝{Style.RESET_ALL}""")


def print_results(results: list, config: Config):
    if not results:
        print(f"\n{Fore.RED}  No results.{Style.RESET_ALL}\n")
        return

    # Sort: both-pass first, then adx-only, then candle-only, then rest; within group by ADX asc
    def sort_key(r):
        if r["both_pass"]:   tier = 0
        elif r["adx_pass"]:  tier = 1
        elif r["candle_pass"]:tier = 2
        else:                 tier = 3
        return (tier, r["adx"])

    results.sort(key=sort_key)

    ts       = datetime.now().strftime("%H:%M:%S")
    n_both   = sum(1 for r in results if r["both_pass"])
    n_adx    = sum(1 for r in results if r["adx_pass"] and not r["both_pass"])
    n_candle = sum(1 for r in results if r["candle_pass"] and not r["both_pass"])

    print(f"\n  [{ts}]  {len(results)} symbols scanned  |  "
          f"{Fore.GREEN}★ {n_both} BOTH{Style.RESET_ALL}  |  "
          f"{Fore.YELLOW}◆ {n_adx} ADX-only{Style.RESET_ALL}  |  "
          f"{Fore.CYAN}● {n_candle} Candle-only{Style.RESET_ALL}\n")

    headers = [
        "#", "Symbol", "LCP", "Chg%",
        "ADX", "+DI", "-DI", "ADXChg%", "DMITrend", "ADXTrend",
        "CndlType", "Strength", "Size", "Vol%", "Comment",
        "3-Candle Pattern", ""
    ]

    rows = []
    for idx, r in enumerate(results, 1):
        ac = _adx_color(r["adx"])
        vc = _vol_color(r["volume_pct"])
        dt_color = Fore.GREEN if r["dmi_trend"] == "Positive" else Fore.RED
        at_color = Fore.YELLOW if r["adx_trend"] == "Neutral" else Fore.WHITE

        rows.append([
            idx,
            f"{Fore.CYAN}{r['symbol']:<12}{Style.RESET_ALL}",
            f"{r['lcp']:>9,.2f}",
            f"{r['pct_change']:>+6.2f}%",
            f"{ac}{r['adx']:>6.2f}{Style.RESET_ALL}",
            f"{r['plus_di']:>5.2f}",
            f"{r['minus_di']:>5.2f}",
            f"{r['adx_change_pct']:>+7.2f}%",
            f"{dt_color}{r['dmi_trend']:<8}{Style.RESET_ALL}",
            f"{at_color}{r['adx_trend']:<10}{Style.RESET_ALL}",
            f"{r['candle_type']:<7}",
            f"{r['candle_strength']:>7.2f}%",
            f"{r['candle_size']:>7.2f}%",
            f"{vc}{r['volume_pct']:>+7.2f}%{Style.RESET_ALL}",
            f"{r['comment']:<16}",
            f"{r['three_candle']:<26}",
            _pass_flag(r),
        ])

    print(tabulate(rows, headers=headers, tablefmt="rounded_outline"))

    # ── Summary block ─────────────────────────────────────────────────
    both   = [r for r in results if r["both_pass"]]
    adx_o  = [r for r in results if r["adx_pass"] and not r["both_pass"]]
    cndl_o = [r for r in results if r["candle_pass"] and not r["both_pass"]]

    if both:
        print(f"\n{Fore.GREEN}  ★  HIGHEST-CONVICTION STRADDLE CANDIDATES  (ADX + Candle both pass){Style.RESET_ALL}")
        print(f"  {'Symbol':<14} {'LCP':>8} {'ADX':>6} {'ADXChg':>8} {'Candle':>8} {'Vol%':>8} {'3-Candle Pattern'}")
        print("  " + "─" * 80)
        for r in both:
            print(f"  {Fore.CYAN}{r['symbol']:<14}{Style.RESET_ALL}"
                  f" {r['lcp']:>8,.2f}"
                  f" {Fore.GREEN}{r['adx']:>6.2f}{Style.RESET_ALL}"
                  f" {r['adx_change_pct']:>+8.2f}%"
                  f" {r['candle_type']:>8}"
                  f" {Fore.GREEN}{r['volume_pct']:>+8.2f}%{Style.RESET_ALL}"
                  f"  {r['three_candle']}")

    if adx_o:
        print(f"\n{Fore.YELLOW}  ◆  ADX-ONLY CANDIDATES  (momentum fading, no Inaction candle today){Style.RESET_ALL}")
        syms = ", ".join(r["symbol"] for r in adx_o)
        print(f"  {syms}")

    if cndl_o:
        print(f"\n{Fore.CYAN}  ●  CANDLE-ONLY CANDIDATES  (Inaction today, ADX not yet Neutral){Style.RESET_ALL}")
        syms = ", ".join(r["symbol"] for r in cndl_o)
        print(f"  {syms}")

    print(f"""
  Legend:
    ★ BOTH   = ADX Neutral + falling  AND  Supply candle + low volume  → prime straddle target
    ◆ ADX    = Momentum exhausting but no Inaction signal today → watch for entry
    ● CNDL   = Inaction signal today but ADX still elevated → higher risk straddle
    Green ADX  < 20  |  Yellow ADX 20–25  |  Red ADX > 25
    Candle Vol%: negative (green) = below 20-day avg → confirms Inaction\n""")


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════

def run_screener():
    config = Config()
    client = OpenAlgoClient(config.OPENALGO_HOST, config.API_KEY)

    print_banner(config)
    log.info("Fetching %d days of history for %d symbols from %s ...",
             config.HISTORY_DAYS, len(config.WATCHLIST), config.EXCHANGE)

    results = []
    for symbol, lot, step in config.WATCHLIST:
        r = analyse_symbol(client, symbol, lot, step, config)
        if r:
            results.append(r)

    print_results(results, config)

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = f"candle_adx_{ts}.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  Full results saved → {out_file}\n")


if __name__ == "__main__":
    run_screener()
