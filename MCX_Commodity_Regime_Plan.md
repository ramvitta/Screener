# Plan: MCX futures and options, plus regime-driven spread selection

## Context
Option_Trades is a read-only FastAPI + Next.js screener on the Definedge broker API. Today it handles NIFTY index options on NFO only, with three strategies (bull put, bear call, iron condor). The aim is to add MCX commodity futures and options on the same broker login and API, and to choose spread strategies from a market regime instead of a fixed filter.

Inputs: the Ep. 46 commodity trend study (gold, silver, copper, crude, natural gas), the vertical-spreads transcript, `SRS_Architecture.md`, `IMPLEMENTATION_PLAN.md` (guardrails), and a code exploration. Order placement stays out of scope; this remains read-only.

---

## Part 1A. Integrating MCX

### What breaks today
- `instruments.py` hardcodes NSE/NFO, `OPTIDX`/`FUTIDX`, a "Nifty 50" spot lookup and `NIFTY_STRIKE_INTERVAL = 50`. MCX options sit on futures, so there is no spot index. The underlying is the future with the matching expiry.
- `pricing.py` is Black-Scholes on spot with a fixed 6.5% rate. MCX needs Black-76 (forward price, no carry term).
- Lot size and multiplier are parsed but never used. P&L is `diff * qty` with no multiplier, and signals are per unit.
- SQLite `option_type CHECK IN ('CE','PE')` blocks futures legs. ClickHouse has an options-only table, no exchange column and `Decimal(10,2)` prices.
- NIFTY is hardcoded in schemas, routes (`/nifty`, `find_spot(rows,"NIFTY")`), the TopBar and the chart labels.
- The session window and the scheduler assume NSE hours. MCX runs into the late evening.
- The option chain makes one quote call per strike and type. This must be rate-limited or cached for more underlyings.

### Phased approach
| Phase | Work | Key files |
|---|---|---|
| 0. Spike (read-only script) | Confirm in `allmaster.csv` the MCX segment code, instrument types for futures and options, lot sizes, tick sizes. Confirm `quotes` and `history` accept MCX tokens, that the account has MCX data, and which MCX options exist with real OI. Nothing else starts until this is answered. | new `scripts/mcx_probe.py` |
| 1. Underlying registry | One `UnderlyingSpec` per symbol: exchange, kind (index or future), option model (BS or B76), strike step, lot size, multiplier, session hours, spread width as a % of price. Make `instruments.py` generic: `find_underlying`, `find_future`, `find_option`, `nearest_expiry` take the spec. The NIFTY helpers become thin wrappers. | `integrations/instruments.py`, new `config/underlyings.py` |
| 2. Pricing and engine | Add Black-76 in `pricing.py` and select by spec. Make `engine.py` and `screener_loop.py` take an underlying, with width in strike steps, not 100 points. Apply the lot multiplier to max profit, max loss and P&L (`positions.py`, `analytics.py`). Add per-exchange hours to the scheduler. | `screener/*`, `workers/*`, `api/routes/positions.py` |
| 3. Schema | SQLite: add exchange, instrument_type, lot_size, multiplier, and allow a FUT leg. This needs a migration, since there is none today. ClickHouse: add an `exchange` column, a `futures_candles` table, and a daily continuous (back-adjusted) series for regime work. Widen price precision. | `db/sqlite.py`, `db/clickhouse.py`, `models/schemas.py` |
| 4. API and UI | Underlying selector, MCX-aware TopBar, expiry strip without the NSE weekly/monthly assumption, lot and multiplier shown in P&L. | `api/routes/{market,chain,charts}.py`, `frontend/components/*`, `lib/types.ts` |

### Sequencing with the guardrails plan
Do M2 and M3 of `IMPLEMENTATION_PLAN.md` first, or at the same time. That covers the iron condor POP and breakeven fix (`engine.py`), the sample-data refusal and `data_source` label (`screener_loop.py`), and the pricing guards. MCX would otherwise inherit those bugs. Both plans edit `engine.py`, `pricing.py`, `schemas.py` and `sqlite.py`, so merge them in one order.

### MCX-specific rules
- MCX options expire before their underlying future and exercise into that future. The screener must show both dates.
- A liquidity gate on every candidate leg: minimum OI and volume, and maximum bid-ask spread as a % of mid. Most MCX contracts are thin, as the study says. Show a "futures only" or "skip" verdict where options fail the gate.
- Use realized-volatility percentile on the futures series as the IV-rank proxy until IV history exists. ClickHouse is empty and `_load_chain_from_clickhouse` returns `None`.

---

## Part 1B. Potential option trades from the Ep. 46 study

The study is a Supertrend(10,3) stop-and-reverse on back-adjusted daily futures over 11 years, with no costs or slippage. It is a behaviour baseline, not a strategy. I use it for one thing: which side of each market is allowed. That is the "commodity profile" in the plan.

| Contract | What the study found | Allowed side | Candidate option trades | Caution |
|---|---|---|---|---|
| **Gold** | Long side made nearly all the points. Best win rate (~49%) and best return to drawdown. Most consistent across years. Shorts lost, and fading short signals gained 32,331 points over 46 trades. | Bullish only | Bull put spread on pullbacks (credit). Bull call spread when IV is low and trend is confirmed. | No bear call spreads or short calls. Short side is the one that failed. |
| **Copper** | Long side has an edge and is the second most consistent. Much of the gain came in 2026. | Bullish | Bull put or bull call spread. | A possible regime or super-cycle effect, so treat the bias as provisional. Option liquidity is likely thin: gate it, otherwise trade the future. |
| **Crude oil** | Slight long bias (worked 2020–23). Shorts failed badly, and fading short signals gained 2,652 points. Roughly equal days long and short. | Bullish when trending, otherwise neutral | Bull put spread in an uptrend. Iron condor only in a low-trend, high-IV range. | The long edge is weak and macro-driven (four years in a row). Event risk is high. |
| **Natural gas** | Only the short side worked, and it is third in consistency. Fading longs gained just 245 points, so not a real edge. | Bearish | Bear call spread (credit). Bear put spread (debit) on a confirmed downtrend. | Very high volatility. Defined risk only, never naked. |
| **Silver** | Both sides made money but results are not consistent across years. The 2026 short side alone beat the other 11 years combined. | No standing bias | Trade only when the regime engine confirms a trend, and size small. Iron condor in a confirmed range. | Edge depends on one year. |

Liquidity ranking, to confirm in the Phase 0 spike: crude oil and gold are likely the most liquid, then silver and natural gas, with copper thinnest.

---

## Part 2. Regime-based spread selection

Source ideas from the vertical-spreads transcript:
- Spreads are directional. Credit spreads win more often (about 65% POP against about 42% for the debit version) and make money in sideways markets where a debit spread does not.
- Use monthly expiry, and a width of roughly 1% of the underlying (300 points on Nifty).
- Time entries with RSI extremes and the RSI moving-average cross. Do not trade when RSI is in the middle ("no man's land").
- Chart the spread itself (a multileg line chart) to place a tight stop. This turns a 2:1 payoff graph into roughly 5:1.
- Re-check risk and reward every day, and exit when the reward left is small.
- Do not chase a gap on RSI alone.
- Allocate 1.5 to 2 times the margin, and take roughly one trade a month.

### Step 1. Identify the regime
New `screener/regime.py`, run on daily and intraday futures series. Four independent axes, each with a tunable threshold:

| Axis | Measure | States |
|---|---|---|
| Trend | ADX(14) plus Supertrend(10,3) direction, EMA20/50 slope | up, down, none |
| Volatility | IV rank where available, otherwise realized-vol percentile (252-day) | low (<30), mid, high (>50) |
| Momentum and location | RSI(14), RSI-MA cross, distance from support or resistance | oversold, neutral, overbought, plus cross state |
| Term structure and events | Futures curve (contango or backwardation), scheduled events (EIA, OPEC, Fed, MCX holidays) | normal, event within the window |

Combine them into one of six regimes.

| Regime | Condition |
|---|---|
| R1 Uptrend | trend up |
| R2 Downtrend | trend down |
| R3 Range, rich vol | trend none, vol high |
| R4 Range, cheap vol | trend none, vol low |
| R5 Breakout or vol expansion | vol rising fast, price outside range |
| R6 Stand aside | conflicting signals or event inside the window |

### Step 2. Actions and decisions from the regime
Each commodity has a permission mask from Part 1B, applied after the regime. Gold R2 is blocked, for example, so it falls to "no trade".

| Regime | Preferred structure | Direction | Entry timing | Exit and management |
|---|---|---|---|---|
| R1 Uptrend, vol mid/high | Bull put spread (credit) | Long | Pullback: RSI near oversold or RSI-MA cross up | Spread-chart stop, take profit at most of the credit, daily risk/reward re-check |
| R1 Uptrend, vol low | Bull call spread (debit) | Long | Breakout above resistance, RSI not extreme | Stop on spread chart, exit on trend flip |
| R2 Downtrend, vol mid/high | Bear call spread (credit) | Short | Bounce: RSI near overbought or RSI-MA cross down | Same as R1 |
| R2 Downtrend, vol low | Bear put spread (debit) | Short | Breakdown below support | Same as R1 |
| R3 Range, rich vol | Iron condor, or iron butterfly | Neutral | Mid-range, RSI neutral | Close early at partial profit, close if a side is breached |
| R4 Range, cheap vol | No option sell. Watch for expansion | None | None | None |
| R5 Breakout | Debit spread in the breakout direction | With the break | After a retest, not on the gap | Tight stop, size down |
| R6 Stand aside | None | None | None | None |

Rules that apply to every trade: monthly expiry (an MCX option expires before its future), one open spread per underlying, size from the allocated-capital rule, a defined loss, and a daily recheck that raises an alert when the reward left is small.

### Step 3. Criteria to qualify each regime and strategy
Starting thresholds, all stored in `screener_rules.params_json` and calibrated later, not hard-coded:

- **Common gates (all strategies):** liquidity gate passes (min OI and volume, max bid-ask % of mid), credit above a minimum, credit below width, max loss within the per-trade risk limit, no event inside the holding window, and `data_source` is live.
- **Credit spreads (R1/R2):** ADX above about 20 and trend direction agrees. Vol mid or high. Short leg delta 0.15 to 0.30. POP at least 0.60. Credit at least about 25–33% of width. RSI at a pullback extreme or an RSI-MA cross in the trend direction.
- **Debit spreads (R1/R2 low vol, R5):** vol low (IV rank under about 30). ADX rising. Price at a breakout. Debit no more than about 40% of width. Reward-to-risk at least about 1.5.
- **Iron condor / butterfly (R3):** ADX below about 20. Vol high (IV rank over about 50). RSI in the 40–60 band. Short strikes outside the recent support and resistance. Combined POP at least about 0.65, using the corrected joint POP.
- **Stand aside (R6):** any hard gate fails, or axes disagree, or an event is inside the window.

### Output shape
Extend the signal payload with `regime`, `regime_inputs` (the axis values), `permission` (allowed side for the commodity) and a "Reason for Trade" that names the regime. This reuses the existing templated rationale generator in `engine.py`.

---

## Delivery order
1. Guardrails M2/M3 items that touch signal math and data provenance.
2. Phase 0 MCX spike, with a go/no-go on which underlyings have usable options.
3. Phases 1–3 (registry, Black-76, schema), then Phase 4 (UI).
4. Regime engine and commodity profile.
5. Backtest on futures history. First reproduce the Ep. 46 Supertrend numbers as a check on the data pipeline, then evaluate the regime rules and calibrate thresholds. The thresholds above are starting points, not validated.

## Verification
- Phase 0: the probe script prints, for each of the five underlyings, the MCX segment string, token, lot size, nearest future, and a live quote and a history sample for it. It lists which options have OI.
- Unit tests: Black-76 against known values and put-call parity, strike-step and ATM rounding per underlying, multiplier in P&L, regime classification on frozen series.
- Regression: NIFTY results are unchanged after the generalisation.
- End to end: start with `scripts/start.sh`, select a commodity, confirm signals carry exchange, multiplier, regime and permission. Confirm a signal is blocked when it is on a disallowed side or fails the liquidity gate.

## Open decisions
1. Which underlyings go in phase 1 of MCX (suggest crude oil and gold first, after the spike shows liquidity).
2. Whether to include futures-only monitoring for thin contracts such as copper.
3. How to source IV history, given the realized-vol proxy is only an interim measure.
