"""
═══════════════════════════════════════════════════════════════════════
 STRADDLE / STRANGLE SCREENER  —  OpenAlgo Edition
 Author : for VS | Date: July 2026
═══════════════════════════════════════════════════════════════════════

 Strategy:
   Short Straddle  → Sell ATM Call + Sell ATM Put (same strike)
   Short Strangle  → Sell OTM Call + Sell OTM Put (equidistant strikes)

 Screen criterion:
   ATM Call LTP ≈ ATM Put LTP  (within ATM_PARITY_PCT variance)
   → When CE ≈ PE at ATM, the market is pricing symmetric risk
     → ideal for delta-neutral straddle entry

 Also shows:
   • Futures LTP (current month)
   • ATM strike, CE & PE premiums, spread %, total straddle premium
   • ±1 step strangle premiums (for context)

 Requirements:
   pip install requests tabulate colorama schedule

 Run:  python str_stra_screener.py
═══════════════════════════════════════════════════════════════════════
"""

import os
import json
import time
import logging
import warnings
from datetime import datetime, date
from typing import Optional

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
log = logging.getLogger("StrStraScreener")


# ══════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

class Config:
    OPENALGO_HOST   = "http://127.0.0.1:5000"
    API_KEY         = os.getenv("OPENALGO_API_KEY", "5f70f56afb6677db1d95aaa85a2e639c71b7b61f32555307d3bd4fe15c83445f")

    TARGET_EXPIRY   = "28JUL2026"   # DDMONYYYY
    EXCHANGE        = "NFO"

    # ATM parity: |CE_ltp - PE_ltp| / midpoint ≤ this %
    ATM_PARITY_PCT  = 2.0           # 2% → tight parity → flag as STRADDLE candidate

    RUN_INTERVAL_SEC = 300          # re-scan every 5 min (0 = once only)

    # ── Watchlist: (Symbol, LotSize, StrikeStep) ────────────────────
    # Lot sizes for NFO July 2026 — verify at nseindia.com/option-chain
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
#  OPENALGO API CLIENT
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
            resp = self.session.post(url, json=payload, timeout=10)
            if resp.status_code == 400 and silent:
                return {}
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError:
            if not silent:
                log.error("Cannot reach OpenAlgo at %s — is it running?", self.host)
            return {}
        except Exception as e:
            if not silent:
                log.error("API error [%s]: %s", endpoint, e)
            return {}

    def get_ltp(self, symbol: str, exchange: str = "NFO") -> Optional[float]:
        data = self._post("/api/v1/quotes", {"symbol": symbol, "exchange": exchange}, silent=True)
        if data.get("status") == "success":
            ltp = data.get("data", {}).get("ltp")
            return float(ltp) if ltp else None
        return None

    @staticmethod
    def expiry_short(expiry: str) -> str:
        """'28JUL2026' → '28JUL26'  (OpenAlgo/Zerodha NFO symbol format)"""
        expiry = expiry.upper().strip()
        if len(expiry) == 9:           # 28JUL2026 → 28JUL26
            return expiry[:5] + expiry[7:]
        return expiry                  # already short

    def get_option_ltp(self, symbol: str, short_expiry: str,
                       strike: float, opt_type: str,
                       exchange: str = "NFO") -> Optional[float]:
        """
        Build OpenAlgo option symbol and fetch LTP.
        Format: SBIN28JUL261050CE
        Strike is formatted as int if whole, else 1 decimal.
        """
        if strike == int(strike):
            strike_str = str(int(strike))
        else:
            strike_str = f"{strike:.1f}"
        sym = f"{symbol}{short_expiry}{strike_str}{opt_type.upper()}"
        log.debug("    Fetching %s @ %s", sym, exchange)
        return self.get_ltp(sym, exchange)

    def get_futures_ltp(self, symbol: str, short_expiry: str,
                        exchange: str = "NFO") -> Optional[float]:
        """Format: SBIN28JUL26FUT"""
        sym = f"{symbol}{short_expiry}FUT"
        return self.get_ltp(sym, exchange)


# ══════════════════════════════════════════════════════════════════════
#  SCREENER CORE
# ══════════════════════════════════════════════════════════════════════

def snap_strike(price: float, step: float) -> float:
    return round(price / step) * step


def screen_symbol(
    client:      OpenAlgoClient,
    symbol:      str,
    lot_size:    int,
    step:        float,
    short_expiry: str,
    config:      Config,
) -> Optional[dict]:
    log.info("  %-14s  lot=%-5d  step=%s", symbol, lot_size, step)

    # ── Futures LTP ───────────────────────────────────────────────────
    fut_ltp = client.get_futures_ltp(symbol, short_expiry, config.EXCHANGE)
    if not fut_ltp:
        log.warning("    No futures LTP — skipping %s", symbol)
        return None

    atm_strike = snap_strike(fut_ltp, step)

    # ── ATM CE & PE ───────────────────────────────────────────────────
    ce_ltp = client.get_option_ltp(symbol, short_expiry, atm_strike, "CE", config.EXCHANGE)
    pe_ltp = client.get_option_ltp(symbol, short_expiry, atm_strike, "PE", config.EXCHANGE)

    if not ce_ltp or not pe_ltp:
        log.warning("    Missing ATM quotes (CE=%s PE=%s) — skipping %s", ce_ltp, pe_ltp, symbol)
        return None

    midpoint  = (ce_ltp + pe_ltp) / 2.0
    spread_pct = abs(ce_ltp - pe_ltp) / midpoint * 100  if midpoint > 0 else 999

    straddle_premium = ce_ltp + pe_ltp
    straddle_value   = straddle_premium * lot_size        # total credit received

    # ── ±1 strike strangle (for context) ─────────────────────────────
    otm_call_strike = atm_strike + step
    otm_put_strike  = atm_strike - step

    otm_ce = client.get_option_ltp(symbol, short_expiry, otm_call_strike, "CE", config.EXCHANGE)
    otm_pe = client.get_option_ltp(symbol, short_expiry, otm_put_strike,  "PE", config.EXCHANGE)

    strangle_premium = (otm_ce or 0) + (otm_pe or 0)
    strangle_value   = strangle_premium * lot_size

    passes = spread_pct <= config.ATM_PARITY_PCT

    log.info("    ATM=%.1f  CE=%.2f  PE=%.2f  spread=%.2f%%  %s",
             atm_strike, ce_ltp, pe_ltp, spread_pct, "✓ PARITY" if passes else "")

    return {
        "symbol":            symbol,
        "lot_size":          lot_size,
        "fut_ltp":           fut_ltp,
        "atm_strike":        atm_strike,
        "ce_ltp":            ce_ltp,
        "pe_ltp":            pe_ltp,
        "spread_pct":        spread_pct,
        "straddle_premium":  straddle_premium,
        "straddle_value":    straddle_value,
        "otm_call_strike":   otm_call_strike,
        "otm_put_strike":    otm_put_strike,
        "otm_ce_ltp":        otm_ce,
        "otm_pe_ltp":        otm_pe,
        "strangle_premium":  strangle_premium,
        "strangle_value":    strangle_value,
        "passes":            passes,
    }


# ══════════════════════════════════════════════════════════════════════
#  DISPLAY
# ══════════════════════════════════════════════════════════════════════

def print_banner(config: Config):
    print(f"""
{Fore.CYAN}╔══════════════════════════════════════════════════════════════════════╗
║   STRADDLE / STRANGLE SCREENER  —  OpenAlgo                        ║
║   Expiry : {config.TARGET_EXPIRY:<10}  |  Exchange : {config.EXCHANGE:<5}                    ║
║   ATM Parity Filter : ≤ {config.ATM_PARITY_PCT:.1f}%  (CE ≈ PE spread)              ║
╚══════════════════════════════════════════════════════════════════════╝{Style.RESET_ALL}""")


def print_results(results: list, config: Config):
    if not results:
        print(f"\n  {Fore.RED}No results.{Style.RESET_ALL}\n")
        return

    # Sort: parity passes first, then tightest spread
    results.sort(key=lambda x: (not x["passes"], x["spread_pct"]))

    ts     = datetime.now().strftime("%H:%M:%S")
    n_pass = sum(1 for r in results if r["passes"])

    print(f"\n  [{ts}]  {len(results)} stocks scanned  |  "
          f"{Fore.GREEN}{n_pass} with ATM parity ≤ {config.ATM_PARITY_PCT}%{Style.RESET_ALL}\n")

    # ── Summary table (straddle candidates) ──────────────────────────
    headers = [
        "#", "Symbol", "Fut LTP", "Lot",
        "ATM", "CE", "PE", "Spread%",
        "Straddle\nPrem", "Straddle\nValue",
        "Strangle\nPrem", "Strangle\nValue",
        ""
    ]

    rows = []
    for idx, r in enumerate(results, 1):
        flag = f"{Fore.GREEN}★ STRADDLE{Style.RESET_ALL}" if r["passes"] else ""
        sp_color = Fore.GREEN if r["passes"] else (Fore.YELLOW if r["spread_pct"] < 5 else Fore.RED)

        rows.append([
            idx,
            f"{Fore.CYAN}{r['symbol']:<12}{Style.RESET_ALL}",
            f"{r['fut_ltp']:>9,.2f}",
            f"{r['lot_size']:>6,}",
            f"{r['atm_strike']:>8,.1f}",
            f"{r['ce_ltp']:>7,.2f}",
            f"{r['pe_ltp']:>7,.2f}",
            f"{sp_color}{r['spread_pct']:>5.2f}%{Style.RESET_ALL}",
            f"{r['straddle_premium']:>7,.2f}",
            f"{r['straddle_value']:>9,.0f}",
            f"{r['strangle_premium']:>7,.2f}" if r["strangle_premium"] else "  —  ",
            f"{r['strangle_value']:>9,.0f}"   if r["strangle_value"]   else "  —  ",
            flag,
        ])

    print(tabulate(rows, headers=headers, tablefmt="rounded_outline"))

    # ── Detail block for passes ───────────────────────────────────────
    passes = [r for r in results if r["passes"]]
    if passes:
        print(f"\n{Fore.GREEN}  ★ STRADDLE CANDIDATES (ATM parity ≤ {config.ATM_PARITY_PCT}%){Style.RESET_ALL}")
        print(f"  {'Symbol':<14} {'ATM':>8} {'CE':>8} {'PE':>8} {'Spread%':>8} "
              f"{'Straddle':>12} {'Lot':>6} {'Credit':>10}")
        print("  " + "─" * 80)
        for r in passes:
            print(
                f"  {Fore.CYAN}{r['symbol']:<14}{Style.RESET_ALL}"
                f" {r['atm_strike']:>8,.1f}"
                f" {r['ce_ltp']:>8,.2f}"
                f" {r['pe_ltp']:>8,.2f}"
                f" {Fore.GREEN}{r['spread_pct']:>7.2f}%{Style.RESET_ALL}"
                f" {r['straddle_premium']:>11,.2f}"
                f" {r['lot_size']:>6,}"
                f" {Fore.GREEN}{r['straddle_value']:>10,.0f}{Style.RESET_ALL}"
            )
        print(f"\n  ★ = Sell 1 lot ATM CE + 1 lot ATM PE for each symbol above.")
        print(f"    Strangle: sell OTM CE (ATM+1step) + OTM PE (ATM-1step) for lower margin.\n")
    else:
        print(f"\n  No symbols met ATM parity ≤ {config.ATM_PARITY_PCT}%.  "
              f"Closest candidates shown in yellow above.\n")


# ══════════════════════════════════════════════════════════════════════
#  MAIN RUNNER
# ══════════════════════════════════════════════════════════════════════

def run_screener():
    config       = Config()
    client       = OpenAlgoClient(config.OPENALGO_HOST, config.API_KEY)
    short_expiry = OpenAlgoClient.expiry_short(config.TARGET_EXPIRY)

    print_banner(config)
    log.info("Expiry: %s  (short: %s)", config.TARGET_EXPIRY, short_expiry)
    log.info("Scanning %d symbols ...", len(config.WATCHLIST))

    results = []
    for symbol, lot_size, step in config.WATCHLIST:
        res = screen_symbol(client, symbol, lot_size, step, short_expiry, config)
        if res:
            results.append(res)

    print_results(results, config)

    # Save JSON snapshot
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = f"str_stra_{ts}.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  Results saved → {out_file}\n")


def main():
    config = Config()
    if config.RUN_INTERVAL_SEC > 0:
        import schedule as sched
        print(f"Auto-scan every {config.RUN_INTERVAL_SEC}s. Ctrl+C to stop.\n")
        run_screener()
        sched.every(config.RUN_INTERVAL_SEC).seconds.do(run_screener)
        while True:
            sched.run_pending()
            time.sleep(1)
    else:
        run_screener()


if __name__ == "__main__":
    main()
