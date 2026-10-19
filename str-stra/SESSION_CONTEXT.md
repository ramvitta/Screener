# Straddle / Strangle Screener — Session Context
**Date:** 2 July 2026  
**Project:** `C:\Ram\Personal\py-scripts\str-stra`  
**OpenAlgo instance:** `http://127.0.0.1:5000`  
**API Key:** `5f70f56afb6677db1d95aaa85a2e639c71b7b61f32555307d3bd4fe15c83445f`

---

## Overview

Built a unified Python screener that uses a live OpenAlgo instance (connected to broker via real-time feed) to identify straddle and strangle opportunities across 49 liquid F&O stocks (NSE Options Liquid Top 50 universe).

The screener runs three sequential passes and merges results into a single table, re-running every 10 minutes.

---

## Universe / Watchlist

Source file: `Liq-50.txt`

49 stocks with highly liquid futures contracts. Lot sizes and strike steps for **July 2026 expiry** are hardcoded in all scripts (one-time activity, update monthly).

```
ABB, ADANIENT, ADANIPORTS, ADANIPOWER, ANGELONE, ASIANPAINT, AXISBANK,
BEL, BHARTIARTL, BHEL, BIOCON, BSE, COALINDIA, COLPAL, DIXON, DMART,
ETERNAL, HAL, HDFCAMC, HDFCBANK, HDFCLIFE, HINDALCO, HINDUNILVR,
ICICIBANK, INFY, ITC, JIOFIN, KALYANKJIL, LT, MARUTI, MAZDOCK, NTPC,
PFC, POLICYBZR, POLYCAB, POWERGRID, RELIANCE, RVNL, SBIN, SIEMENS,
SUNPHARMA, SUZLON, TCS, TMPV, TRENT, UNITDSPR, WAAREEENER, WIPRO, YESBANK
```

Full lot size + strike step table is in `unified_screener.py` → `Config.WATCHLIST`.

---

## Input Data Sources (from Zoneweb platform)

### 1. `Adx_Digger.csv`
Scanner output from Zoneweb's ADX Digger preset applied to Options Liquid Top 50.  
Columns: `Scrip, Sector, Segment, Market Cap, LCP, Price Change%, ADX, DMI+, DMI-, ADX Change, DMI Diff, DIM Trend, ADX Trend`

### 2. `Candle_Volume_Analysis_Scanner (2).csv`
Scanner output from Zoneweb's Candle Volume Analysis Scanner.  
Settings used: NSE | Options Liquid Top 50 | Daily | Three-Candle Pattern | Volume | Look-Back: 0 | Preset: `str-opt50-today`  
Columns: `Scrip, Sector, Segment, Market Cap, LCP, % Change, Candle Strength, Candle Type, Candle Size, Volume%, Comment`

### 3. `str-stra-candle-volume-scanner.png`
Screenshot of the Zoneweb scanner UI showing the above settings.

---

## Three-Pass Screening Logic

### Pass 1 — ADX Screener

**Purpose:** Find stocks where directional momentum is exhausting (ADX falling, trend Neutral).  
These are candidates moving into a range-bound phase — ideal for premium selling.

**Data source:** `GET /api/v1/history` (NSE daily OHLCV, 90 calendar days)  
**Computed in Python using Wilder's smoothing:**

| Metric | Formula |
|---|---|
| True Range | `max(H-L, |H-Cprev|, |L-Cprev|)` |
| +DM / -DM | Directional movement, Wilder-smoothed over 14 bars |
| +DI / -DI | `100 × DM_smooth / ATR` |
| DX | `100 × |+DI - -DI| / (+DI + -DI)` |
| ADX | Wilder smooth of DX over 14 bars |

**ADX Trend classification** (3-bar window):
- All 3 bars rising → `"Trending"`
- All 3 bars falling → `"Weakening"`  
- Mixed → `"Neutral"`

**ADX Filter passes when:**
- `ADX_change < 0` (ADX is falling, 1-bar)
- `ADX_trend == "Neutral"` (not cleanly trending or weakening)

**Columns output:** ADX, +DI, -DI, ADX Chg%, DMI Diff%, DMI Trend, ADX Trend, ADX✓

---

### Pass 2 — Candle-Volume "Inaction" Screener

**Purpose:** Find stocks with weak selling — Supply candle but below-average volume.  
This is the "Inaction" signal: price moved down but without conviction → likely range.

**Data source:** Same OHLCV DataFrame as Pass 1 (no extra API call).  
**20-day rolling averages** for body size and volume.

| Column | Formula |
|---|---|
| Candle Type | `"Supply"` if `close < open`, else `"Demand"` |
| Candle Strength | `\|close - open\| / (high - low) × 100` — body as % of full range |
| Candle Size | `\|close - open\| / mean(\|body\|, 20d) × 100` — relative body size |
| Volume % | `(volume - mean(volume, 20d)) / mean(volume, 20d) × 100` |
| Comment | `"Inaction"` if Supply AND Vol% < 0 |
| | `"Action"` if Supply AND Vol% ≥ 0 |
| | `"Quiet Demand"` if Demand AND Vol% < 0 |
| | `"Active Demand"` if Demand AND Vol% ≥ 0 |

**Three-Candle Pattern** (last 3 bars):
- `"Inaction after Demand"` — Inaction candle following at least one Demand candle
- `"Consecutive Inaction"` — Multiple Inaction candles in a row
- Otherwise describes current candle type

**Candle Filter passes when:** `comment == "Inaction"`

**Columns output:** Candle Type, Strength, Size, Vol%, Comment, 3-Candle Pattern, Candle✓

---

### Pass 3 — Straddle Identification (ATM Parity)

**Purpose:** Find stocks where ATM Call LTP ≈ ATM Put LTP (within 2%).  
When CE ≈ PE at ATM, market is pricing symmetric risk → ideal delta-neutral straddle entry.

**Data source:** Live NFO quotes via OpenAlgo `/api/v1/quotes`

**Symbol formats used (OpenAlgo / Zerodha NFO):**
- Futures: `SBIN28JUL26FUT`
- Options: `SBIN28JUL261050CE`, `SBIN28JUL261050PE`
- Expiry short form: `28JUL2026` → `28JUL26`

**Process:**
1. Fetch Futures LTP → snap to ATM strike (round to strike step)
2. Fetch ATM CE LTP and ATM PE LTP
3. Compute parity: `|CE - PE| / ((CE + PE) / 2) × 100`
4. Also fetch OTM CE (ATM + 1 step) and OTM PE (ATM - 1 step) for strangle context

**Straddle Filter passes when:** `spread_pct ≤ 2.0%` (configurable)

**Columns output:** Fut LTP, ATM Strike, CE, PE, Spread%, Straddle Premium, Straddle Value (₹), Strangle Premium, Strangle Value (₹), Parity✓

---

## Signal Tiers (Combined)

| Tier | ADX✓ | Candle✓ | Parity✓ | Action |
|---|---|---|---|---|
| `★★★ PRIME` | ✓ | ✓ | ✓ | Sell straddle — all signals aligned |
| `★★  READY` | ✓ | ✓ | ✗ | Watch — parity not tight yet |
| `★   WATCH` | ✓ or ✗ | ✓ or ✗ | any | Context only |
| `—` | ✗ | ✗ | ✗ | No signal |

**PRIME interpretation:**  
- Momentum is fading (ADX Neutral + falling)  
- Market is showing no directional commitment (Inaction candle)  
- Options market is pricing symmetric risk (CE ≈ PE)  
→ Sell 1 lot ATM CE + 1 lot ATM PE  
→ Strangle alternative: Sell OTM CE (ATM+step) + OTM PE (ATM-step) for lower margin requirement

---

## Scripts Built

### `str_stra_screener.py`
Standalone straddle screener (Pass 3 only).  
Fetches live futures + ATM CE/PE quotes and flags ATM parity opportunities.  
Runs every 5 minutes by default.

### `candle_adx_screener.py`
Standalone ADX + Candle screener (Pass 1 + 2 only).  
Fetches OHLCV history, computes all indicators, flags Inaction + ADX Neutral stocks.  
Single run (no scheduler).

### `unified_screener.py` ← **PRIMARY SCRIPT**
All three passes merged into one. Single output table. Runs every 10 minutes.

**Key architecture decisions:**
- OHLCV history fetched in parallel (`ThreadPoolExecutor`, 8 workers) — reduces fetch time from ~50s to ~10s
- Pass 1 and Pass 2 share the same DataFrame (one history fetch per symbol)
- Pass 3 (live NFO quotes) runs serially to avoid broker rate limits
- Auto-probes `/api/v1/history` at startup to detect working interval format (D, 1D, 1d, etc.) — different brokers use different values
- Results saved as timestamped JSON after every run

---

## OpenAlgo API Endpoints Used

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/history` | POST | Daily OHLCV history (NSE) |
| `/api/v1/quotes` | POST | Live LTP for futures + options (NFO) |

**History endpoint payload:**
```json
{
  "apikey": "...",
  "symbol": "SBIN",
  "exchange": "NSE",
  "interval": "D",
  "start_date": "2026-04-01",
  "end_date": "2026-07-02"
}
```

**Quotes endpoint payload:**
```json
{
  "apikey": "...",
  "symbol": "SBIN28JUL26FUT",
  "exchange": "NFO"
}
```

---

## Known Issue (as of session end)

`/api/v1/history` returning `400 BAD REQUEST` for all symbols.

**Root cause:** The `interval` parameter value or date field names don't match what this OpenAlgo/broker combination expects.

**Fix applied:** `probe_history_api()` method added to `unified_screener.py`.  
On startup it tries 9 different interval/param combinations and caches the one that works.

**If probe also fails — checklist:**
1. Open `http://127.0.0.1:5000` → confirm broker shows **"Connected"**
2. Go to Settings / Admin → **Download Master Contracts** → wait for completion
3. Run this diagnostic one-liner to see the raw error:
```python
import requests
r = requests.post("http://127.0.0.1:5000/api/v1/history",
    json={"apikey": "5f70f56afb6677db1d95aaa85a2e639c71b7b61f32555307d3bd4fe15c83445f",
          "symbol": "SBIN", "exchange": "NSE",
          "interval": "D", "start_date": "2026-06-01", "end_date": "2026-07-01"})
print(r.status_code, r.text)
```
4. Paste output to continue debugging

---

## Configuration Reference (`unified_screener.py → Config`)

```python
OPENALGO_HOST     = "http://127.0.0.1:5000"
TARGET_EXPIRY     = "28JUL2026"   # update monthly
HISTORY_DAYS      = 90            # calendar days of OHLCV to fetch
MAX_WORKERS       = 8             # parallel history threads
ADX_PERIOD        = 14            # Wilder ADX period
ADX_TREND_WINDOW  = 3             # bars for Neutral/Trending/Weakening
ADX_PREFERRED_MAX = 25.0          # highlight stocks below this ADX
AVG_PERIOD        = 20            # lookback for candle + volume averages
ATM_PARITY_PCT    = 2.0           # |CE-PE|/mid ≤ this % → parity pass
RUN_INTERVAL_MIN  = 10            # 0 = run once, 10 = every 10 min
```

---

## Pre-existing Scripts (for reference)

| Script | Purpose |
|---|---|
| `hedge_screener-v6.py` | Long Future + Delta-40 Put hedge screener (separate strategy) |
| `openalgo_debug.py` | OpenAlgo connectivity + symbol format diagnostic |
| `openalgo_fix.py` | Master contracts download + broker-specific symbol format finder |
| `get_openalgo_token.py` | API key helper |

---

## Next Steps (pending)

1. **Fix `/api/v1/history` 400 error** — run diagnostic one-liner above, identify correct interval format
2. **Validate ADX + Candle output** against Zoneweb CSV exports (cross-check numbers)
3. **Tune `ATM_PARITY_PCT`** — start at 2%, tighten to 1% for fewer but higher-quality signals
4. **Add IV column** to Pass 3 — back-solve implied volatility from live CE/PE premiums using Black-Scholes (code exists in `hedge_screener-v6.py → BS class`)
5. **Add margin estimate** — lot size × SPAN margin approximation for position sizing
6. **Monthly update** — update lot sizes + strike steps in `Config.WATCHLIST` when NSE revises contracts
