# Plan: RMI Trend Sync Strategy — add to the screener

## Context

The RMI Trend Sync strategy synchronizes short-term momentum with the broader
market trend, entering only when both forces are aligned:

- **RMI (Relative Momentum Index)** — a variation of the RSI that accounts for
  momentum duration (it compares `close[i]` vs `close[i - m]`, not bar-to-bar).
- **MFI (Money Flow Index)** — a volume-weighted RSI over typical price, gauging
  the strength of price movement.
- **Composite momentum score** — derived from RMI + MFI; entry requires a
  *shift to Positive* (buying pressure increasing relative to selling).
- **RWMA (Range Weighted Moving Average) bands** — noise filter; price must be
  favorably positioned relative to the bands.
- **SuperTrend filter** — defines the longer-term trend direction ("Sync" =
  short-term momentum aligned with it) and supplies the volatility-based
  dynamic trailing stop for exits.
- **Exit rules** — (1) SuperTrend trailing stop breach, (2) composite score
  flips to Negative.

Recommended settings from the strategy writeup: trade direction "Both"
(switch to Long/Short to match the macro environment), **4H timeframe**
(metrics reflect 4H performance).

Goal: screen it across the F&O watchlist exactly like the other strategies —
identify trades from real broker data, log accepted/rejected signals with full
decision inputs into the signal history, feed the strategy-family UI, alerts,
and detail charts. Order placement stays out of scope; this remains read-only.

The integration pattern to mirror is the **str-stra port** (pure computation
module → worker loop → scheduler → `screener_signals` persistence → frontend
family tile), not the credit-spread engine (which is option-chain based — this
strategy needs no option chain at all).

---

## Part 1. Concrete rules (interpreting the prose into testable logic)

The writeup leaves several definitions open. Each decision below is
deliberate, documented, and exposed as a parameter so it can be tuned without
code changes.

### 1.1 Indicators (all computed on closed bars only — no repainting)

**RMI** (Constance Brown's momentum-duration RSI), defaults `rmi_length=14`,
`rmi_momentum=5`:

```
mom[i]   = close[i] - close[i - m]          # m = rmi_momentum
U[i]     = max(mom[i], 0)                   D[i] = max(-mom[i], 0)
SU, SD   = Wilder-smooth(U, D, rmi_length)  # same smoothing helper as str_stra.wilder()
RMI      = 100 * SU / (SU + SD)             # 0..100; with m=1 it degenerates to RSI
```

**MFI** (classic Quong/Soudack), default `mfi_period=14`:

```
TP[i]  = (high[i] + low[i] + close[i]) / 3
MF[i]  = TP[i] * volume[i]
PMF    = sum of MF where TP rising over period; NMF = sum where TP falling
MFI    = 100 - 100 / (1 + PMF / NMF)        # NMF == 0 → 100
```

**Composite momentum score** — RMI and MFI are both 0–100 oscillators centred
at 50, so the composite is their normalized average:

```
score = ((RMI - 50) + (MFI - 50)) / 2        # range [-50, +50]
score_positive when score > score_threshold  (default 0.0)
```

`score > 0` means net buying pressure; `score < 0` net selling. A positive
threshold (e.g. 5.0) can demand margin instead of a knife-edge cross.

**RWMA** — Range Weighted Moving Average, default `rwma_period=20`,
`rwma_band_mult=1.0`: each bar's close is weighted by that bar's share of the
total range over the window (high-activity bars dominate):

```
w[i]    = (high[i] - low[i]) / Σ(high[j] - low[j])    over the window
RWMA    = Σ(close[i] * w[i])
band    = RWMA ± rwma_band_mult * mean(range)          # RWMA bands
band_pos = (close - lower) / (upper - lower) * 100      # where price sits in the band, 0–100
```

Trend-alignment gate (keep it simple and robust): **long requires
`close > RWMA`**, short requires `close < RWMA`. `band_pos` is logged in every
signal for context and used to rank same-direction candidates.

**SuperTrend**, defaults `st_period=10`, `st_multiplier=3.0` (classic):

```
ATR        = Wilder ATR(st_period)
basic_up   = (H+L)/2 + st_multiplier * ATR      basic_dn = (H+L)/2 - st_multiplier * ATR
final bands carry forward until crossed; trend flips when close crosses the band
direction  = +1 (uptrend) | -1 (downtrend)
line       = final lower band in an uptrend, final upper band in a downtrend
```

The SuperTrend **line value is the strategy's dynamic trailing stop** — it
tightens (ratchets) as price moves, exactly as the exit rule describes.

### 1.2 Entry rules (evaluated on the last *closed* bar)

A **LONG** signal fires when all three hold on the last closed bar:

1. **Momentum Shift** — `score > threshold` while `score_prev ≤ threshold`
   (the composite shifted to Positive on this bar).
2. **Trend Alignment** — `close > RWMA` (price favorably positioned vs the
   RWMA bands).
3. **Trend Sync** — SuperTrend direction is up (`+1`), i.e. short-term
   momentum synchronized with the longer-term trend.

**SHORT** is the mirror (`score < -threshold` fresh shift, `close < RWMA`,
SuperTrend down). The **trade-direction setting** (`rmi_trend_direction`:
`both` | `long` | `short`, default `both`) gates which side is emitted — per
the writeup, switch to `long` in a bull macro, `short` in a bear one.

### 1.3 Exit rules (what the screener logs; v1 is informational)

The screener is a signal generator, not a position monitor (consistent with
every other strategy here — exits are managed by the user via the positions
tab). But because this strategy's exits are *rule-defined*, every signal
carries its own exit plan so the trade can be managed mechanically:

- **Dynamic trailing stop** = the current SuperTrend line value, stamped into
  the signal (`trail_stop`). It recomputes every run.
- **Momentum reversal level** — the signal notes the score flip condition
  (`score < 0` for a long) as the secondary exit.

Phase-2 extension (tracked below in Part 5): cross-run state so the worker
detects, on later runs, that a previously-signaled symbol's momentum flipped
or price crossed the trailing stop, and emits an **exit signal** (same
`screener_signals` table, `reject_reason`-style marker or a `direction=exit`
payload) plus an alert.

### 1.4 What is *not* in v1 scope

- No option legs — entry is a directional underlying/futures trade; signals
  chart the stock future, like `adx_direction` does today.
- No `position_logs` integration — `rmi_trend_sync` stays screener-only (the
  position-log strategy-type trigger allows only the five option types).
- No backtest (`strategy_backtests`) wiring in v1; noted as future work.

---

## Part 2. Data needed to identify trades

| Data | Source | Notes |
|---|---|---|
| 1-minute equity OHLCV per watchlist symbol | Definedge `/history` (`minute`) | The only intraday timeframe the API serves (`minute`, `day`, `tick` — no native 4H). **4H bars are aggregated client-side.** |
| Daily OHLCV (1D mode) | Definedge `/history` (`day`) | Cheaper alternative timeframe mode, same as str-stra uses. |
| Nearest stock-future expiry per symbol | Instrument master | Needed so the signal's detail view can chart the underlying future (`/charts/stock/transforms`), and for a live futures LTP in the payload. |
| Live futures LTP (optional, 1 quote/symbol) | Definedge `/quotes` | Payload freshness (`fut_ltp`), mirrors str-stra. |
| Universe | str-stra `WATCHLIST` (49 F&O stocks) by default; overridable via `RMI_TREND_UNIVERSE` env (comma-separated symbols) | Stocks only in v1 — the NIFTY index has no volume on its candles, and MFI needs volume. |

**4H aggregation** — session-anchored buckets at 09:15 (Indian market): bar 1
= 09:15–13:15 (240 min), bar 2 = 13:15–15:30 (135 min, the session's tail).
Session anchoring avoids zero-volume overnight segments inside a single bar
(which would distort MFI) and doubles bar density vs midnight-anchored
TradingView buckets. The aggregation is a pure function
(`aggregate_intraday(minute_candles, bucket_minutes, anchor)`) so it is
unit-testable. The trailing partial bar is treated as *forming*: signals are
only emitted off fully-closed bars (a fresh momentum cross that appears
mid-bar isn't confirmed until the 4H bar closes — no repaint).

**Warmup / history depth** — Wilder smoothing (RMI, ATR) needs a long runway
to converge; SuperTrend(10) stabilizes around ~60 bars. Target ≥ **150 closed
4H bars** ≈ 75 trading days ≈ **~105 calendar days of 1-minute data**. That
fits inside Definedge's ~180-day equity-history cap (`history_equity_max_days_back`).
Concretely ~39,000 minute rows per symbol — one history call per symbol.

**Load per run** — 49 symbols × (1 history call + 1 futures quote) ≈ 50
calls, throttled at the existing `history_rps=3` (≈ 20 s of fetches + parse).
A 4H-bar strategy's state only changes when a bar closes (≤ 2×/day/symbol),
so the loop default interval is **60 minutes** (configurable) — enough to
pick up each closed bar promptly, refresh trailing stops, and stay far away
from rate limits. A SQLite/ClickHouse cache of aggregated 4H bars (so runs
only fetch the increment) is a Phase-3 optimization, not a v1 need.

**Degraded handling** — identical contract to every other loop: if Definedge
is unauthenticated/unreachable or returns no candles, the run emits **no
signals** and records a degraded `screener_runs` row. Never fabricate data.

---

## Part 3. Backend implementation

Mirror the str-stra integration, component by component.

### 3.1 Pure computation module — `backend/app/screener/rmi_trend.py` (new)

List-in/list-out, no network, no pandas — the pattern that makes
`str_stra.py` unit-testable. Contents:

- `wilder(values, period)` — reuse `str_stra.wilder` (import it; don't copy).
- `rmi_series(closes, length, momentum)` → series of `Optional[float]`.
- `mfi_series(highs, lows, closes, volumes, period)` → series.
- `momentum_score_series(rmi, mfi)` → series in [-50, +50].
- `rwma_series(highs, lows, closes, period)` → (rwma series, band_mult →
  bands derived alongside).
- `supertrend_series(highs, lows, closes, period, multiplier)` →
  (line series, direction series) — carry-forward band logic.
- `aggregate_intraday(minute_candles, bucket_minutes=240, anchor="09:15")`
  → 4H candles (session-anchored), pure.
- `RmiTrendParams` dataclass: all knobs from 1.1 + direction + thresholds.
- `evaluate(candles, params)` → `Optional[dict]` (None when warmup
  insufficient — mirrors `technical_metrics`): every computed field
  (`rmi`, `mfi`, `score`, `score_prev`, `rwma`, `band_pos`, `supertrend`,
  `st_direction`, `atr`, `trail_stop`, `close`, `pct_chg`) plus the three
  booleans (`momentum_shift`, `trend_aligned`, `st_aligned`) and the verdict
  (`long_entry` / `short_entry` / `none`). One dict feeds signal-building,
  decision_json, and the reject reasons.

Constants exported like `str_stra.py` does (for decision_json params):
`RMI_LENGTH=14`, `RMI_MOMENTUM=5`, `MFI_PERIOD=14`, `RWMA_PERIOD=20`,
`ST_PERIOD=10`, `ST_MULTIPLIER=3.0`, `SCORE_THRESHOLD=0.0`, `MIN_BARS=150`.

### 3.2 Worker loop — `backend/app/workers/rmi_trend_loop.py` (new)

Structural copy of `str_stra_loop.py`:

1. Gate on `settings.rmi_trend_enabled`.
2. `ensure_authenticated()` + master download → degraded run on failure.
3. For each watchlist symbol (rate-limited): fetch 1-min history for
   `rmi_trend_history_days`, aggregate to 4H, `evaluate()`.
4. Build signals per the verdict:
   - `signal_id = f"rmi-{run_id[:8]}-{symbol.lower()}-{long|short}"` (stable,
     deep-linkable, mirrors `strstra-...`).
   - `strategy_type = "rmi_trend_sync"`; `expiry` = nearest stock-future
     expiry (for charting); `legs = []` (no option legs, like adx_direction).
   - `reason` — templated, e.g.:
     *"RELIANCE LONG — momentum score shifted to +12.4 (RMI 68.2, MFI 58.1);
     price 2,984 above RWMA 2,951 (band pos 78%); SuperTrend up at 2,912
     (ATR 38). Trailing stop 2,912; exit also if score flips negative."*
   - `signal_json` = the full metrics dict (+ forming-bar snapshot) — the
     detail view's raw payload.
   - `decision_json` = `{"params": {...all indicator params, timeframe,
     direction, history_days}, "computed": {...}, "reject_reason": null}` —
     the decision-point record the history page reconstructs.
5. Persist: `screener_runs` row (ok/degraded) + `screener_signals` rows
   (accepted; and rejected candidates with the failing gate as
   `reject_reason` — e.g. `no_momentum_shift`, `rwma_not_aligned`,
   `supertrend_against`, `insufficient_history` — so the history page shows
   *near-miss* setups, exactly like the credit-spread rejects).
6. Insert a `screener_signal` alert per accepted signal (the credit-spread
   loop does this; str-stra currently doesn't — this new loop follows the
   richer behaviour, and the alerts feed + `/ws/alerts` push picks them up).
7. Status dict (`get_status()`) for the degraded/ok message, mirroring
   `str_stra_loop._status`.

Rejected-candidate logging: emit a reject row for a symbol whose direction
gate matches and *some* (not all) conditions hold — i.e. when momentum
shifted but trend didn't sync (`supertrend_against`) — so the user can see
what the filter removed. (Cap it: only log rejects when ≥ 2 of 3 gates pass,
else the table drowns.)

### 3.3 Config — `backend/app/config.py` (edit)

```python
# --- RMI Trend Sync screener (Definedge) ---
rmi_trend_enabled: bool = True
rmi_trend_interval_minutes: int = 60
rmi_trend_timeframe: str = "4H"            # "4H" (minute-aggregated) | "1D"
rmi_trend_direction: str = "both"         # both | long | short
rmi_trend_history_days: int = 110         # calendar days of 1-min data (≈150 4H bars)
rmi_trend_universe: str = ""               # comma-separated override; empty → str-stra WATCHLIST
rmi_trend_rmi_length: int = 14
rmi_trend_rmi_momentum: int = 5
rmi_trend_mfi_period: int = 14
rmi_trend_rwma_period: int = 20
rmi_trend_rwma_band_mult: float = 1.0
rmi_trend_st_period: int = 10
rmi_trend_st_multiplier: float = 3.0
rmi_trend_score_threshold: float = 0.0
```

### 3.4 Schema/enum — `backend/app/models/schemas.py` (edit)

Add `rmi_trend_sync = "rmi_trend_sync"` to `StrategyType` (comment it as
screener-only, like the str-stra families). **No SQLite migration**:
`screener_signals.strategy_type` is free text (the CHECK constraint lives
only in `screener_rules`, which stays untouched), and `position_logs`'s
trigger whitelist is intentionally not extended (v1 is screener-only).

### 3.5 Scheduler + startup — `scheduler.py`, `main.py` (edit)

Register `run_rmi_trend_job` on its own interval (id `rmi_trend_run`,
`next_run_time=None`), kick the first run at startup alongside
`run_str_stra_job()`, and extend the startup log line.

### 3.6 Routes — no new endpoints needed

Everything reuses the existing surface:
- `GET /api/v1/screener/history?strategy_type=rmi_trend_sync` → per-family page.
- `GET /api/v1/screener/history/counts` → home-tile live count (free).
- `GET /api/v1/screener/history/{id}` → detail (signal_json/decision_json).
- `POST /api/v1/charts/stock/transforms` → underlying-future chart for the
  detail view (already handles `legs=[]` for adx_direction).
- `GET /api/v1/alerts` + `/ws/alerts` → feed/push.

### 3.7 Tests — `backend/tests/unit/test_rmi_trend.py` (new)

Pure tests, `test_str_stra.py` conventions (synthetic lists, `math.isclose`):

- **RMI** — hand-computed small series; `m=1` equals RSI; momentum-duration
  behaviour (m=5 delays the turn); bounds 0–100.
- **MFI** — hand-computed TP/MF series; all-rising-TP → 100; NMF=0 guard.
- **Score** — symmetry around 0; `score_prev`/cross detection.
- **RWMA** — range-share weights sum to 1; a big-range bar dominates;
  band_pos 0/100 at the band edges.
- **SuperTrend** — synthetic uptrend → direction +1 and the line ratchets
  (never loosens) as price rises; trend flip on a sharp reversal; hand-checked
  against a small known series.
- **aggregate_intraday** — 09:15 anchoring, 240-min bucketing, partial tail
  bar marked forming, OHLCV merge correctness (volume sums, high=max, …).
- **evaluate** — a constructed series that passes all three long gates
  (momentum cross while above RWMA in a SuperTrend uptrend) → `long_entry`;
  same series with SuperTrend flipped down → reject `supertrend_against`;
  warmup-insufficient → None.
- Loop-level signal builder tests (no network): metrics dict in → signal rows
  out (id scheme, reason text, decision_json shape) — mirror the
  `_build_signals` pattern being pure/testable.

---

## Part 4. Frontend integration

| File | Change |
|---|---|
| `lib/types.ts` | Add `"rmi_trend_sync"` to `StrategyType`; extend `SignalDecisionComputed` with the new optional fields (`rmi`, `mfi`, `momentum_score`, `score_prev`, `rwma`, `band_pos`, `supertrend`, `st_direction`, `atr`, `trail_stop`, `timeframe`, `direction`); add an `RmiTrendSignalJson` interface (StrStraSignalJson-style raw metrics + `legs: []`). |
| `lib/strategies.ts` | New family tile `rmi_trend_sync` — label "RMI Trend Sync", tagline "Momentum synchronized with trend", description of the RMI+MFI score / RWMA / SuperTrend layers; add to `VALID_STRATEGY_TYPES`. Home tile + count appear automatically (`STRATEGY_FAMILIES` + `history/counts`). |
| `lib/format.ts` | `formatStrategyLabel` override: `rmi_trend_sync → "RMI Trend Sync"`. |
| `lib/signalMetrics.ts` | `decisionMetrics` branch for the family: Score (`+12.4 (↑ from −3.1)`), RMI, MFI, RWMA + band pos, SuperTrend line + direction, ATR, Trailing stop. |
| `components/SignalCard.tsx` | Add the `rmi_trend_sync` entry to `STRATEGY_STYLES` (the `Record<StrategyType,...>` makes TS enforce this). |
| `components/SignalDetailView.tsx` | Add the type to the stock-chart set (chart the underlying future via `fetchStockChartTransforms`, `legs=[]` — same path as adx_direction). |
| `components/StrategySignalsPage.tsx`, `SignalHistoryRow`, `HomeView` | No change — driven by the family registry + counts. |

---

## Part 5. Phasing

| Phase | Deliverable | Verify |
|---|---|---|
| **1. Pure core** | `rmi_trend.py` (indicators + aggregation + evaluate) + full unit tests | `pytest tests/unit/test_rmi_trend.py` green offline |
| **2. Pipeline** | `rmi_trend_loop.py` + config + scheduler + startup + StrategyType enum; signals/rejects/alerts persisted | Run backend with Definedge creds → rows in `screener_runs`/`screener_signals`/`alerts`; degraded run when broker down |
| **3. UI** | Frontend wiring (types, family, metrics, detail) | Home tile with live count; family page lists accepted + rejected with reasons; detail charts the stock future |
| **4. Exit tracking** (next) | Cross-run state (last accepted entry per symbol) → emit exit rows/alerts on momentum flip or SuperTrend breach; optional 4H-bar cache to slash per-run fetches | Exit signals appear in history when conditions flip |
| **5. Optional** | 1D-timeframe mode toggle; backtest wiring into `strategy_backtests`; NIFTY-index support (synthetic/absent volume → MFI-neutral scoring) | — |

Sequencing note: Phase 1 is fully offline and independent; Phase 2 touches
only additive files plus three two-line edits (config, scheduler, main);
Phase 3 is additive frontend. No DB migration, no new route, no changes to
the credit-spread engine — blast radius is minimal, matching how str-stra
landed.

## Open decisions logged

1. **Composite score** = mean of (RMI−50, MFI−50) — both are 0–100
   oscillators; average keeps the score symmetric and interpretable
   (−50…+50). Alternative (require *both* RMI>50 and MFI>50) is stricter and
   can be added as a `score_mode` param later.
2. **RWMA** = close weighted by range share; bands = ±1.0 × mean range.
   Alignment gate is `close` vs the RWMA line (bands logged, not gated) —
   keeps the entry rule from the writeup ("price favorably positioned")
   without over-constraining.
3. **4H bars session-anchored at 09:15** (not midnight-anchored) — avoids
   overnight zero-volume inside bars (MFI distortion) and halves history
   needs. The writeup's 4H metrics are TradingView's; anchoring choice is
   documented here and parameterized if we want TV parity later.
4. **Closed-bar-only evaluation** — a momentum shift is confirmed when a 4H
   bar closes; the forming bar's metrics ride along in the payload as
   context. This is the no-repaint contract; the loop's 60-min cadence picks
   up each close promptly.
5. **Universe = stocks only** (index has no volume → no MFI); default list
   reuses the str-stra `WATCHLIST` so the two stock screeners stay aligned.
6. **Alerts on** for this family (credit-spread behaviour) even though
   str-stra currently skips alerts — "all that we do for the other
   strategies" includes the alerts feed and ws push.