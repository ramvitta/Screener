# Feature Charts — Implementation Plan

> Branch: `feature-charts`
> Status: **Awaiting final review before implementation** (nothing implemented yet).
> This document is the source of truth for the three workstreams agreed with the user.

---

## 1. Overview

Three independent workstreams on the `feature-charts` branch:

| # | Workstream | Type | Size |
|---|---|---|---|
| 1 | Signal "call" history + decision points + rejection audit | Backend + Frontend | Large |
| 2 | Richer signal-detail charts (all legs, 5-min default, payoff) | Frontend only | Small |
| 3 | P&F / Renko charts via openalgo-charts (+ candlestick migration) | Backend + Frontend + ClickHouse | Large |

The three are independent and can be implemented and reviewed one at a time, in the order below.

---

## 2. Confirmed scope decisions (from user answers)

These are locked; implement exactly as stated unless a reviewer overrides during review.

### Workstream 1 — Signal history

1. **"Calls" = screener signals** (the generated recommendations), not LLM API calls.
2. **Track BOTH accepted and rejected signals**, going forward. Rejected signals record *why* they were rejected + all available data parameters at the point of rejection.
3. **Keep existing `alerts` rows as-is** — do **not** migrate/backfill them into the new history tables. New tracking starts fresh from the first run after this feature ships.
4. **Retention = 1 year.** No automatic pruning. Pruning is a manual, admin-approved action (see §5.6).
5. **`run_id` grouping** — every 5-min screener run writes a run row, even when it produced zero signals, so the history page can show "nothing found this run."
6. **New `/history` route** + a `TopBar` nav link next to "Admin".

### Workstream 2 — Signal charts

7. **Payoff panel** is wired into the signal-detail page (reuse existing `PayoffChart` + `fetchPayoff`).
8. **Render all legs dynamically** (future + N legs + combined). Iron condor → 6 panels (future + 4 legs + combined).

### Workstream 3 — P&F / Renko + openalgo-charts

9. **Architecture: Python computes, openalgo-charts renders.** Backend has pure, tested `build_renko()` / `build_point_and_figure()` functions; the frontend feeds the pre-computed bricks/columns to openalgo-charts (renderer only).
10. **Box/reversal parameters are user-selectable in the UI**, with defaults:
    - **Base timeframe**: fixed 1-minute for both P&F and Renko, independent of the candlestick interval selector (default 5-min).
    - **Reversal = 3** for both P&F and Renko. (Renko `reversal=3` means price must retrace `3 × box` before a reversal brick forms — confirmed with user.)
    - **Box size** is a percentage of the series' own reference price, selectable from a fixed preset set: **`0.1%`, `0.25%`, `0.5%`, `1%`, `3%`**.
      - Default **`0.1%`** for all charts **except** the combined-premium chart, which defaults to **`0.5%`**.
      - "reference price" = the series' own current price (spot / future / CE / PE / combined premium).
    - ATR auto-sizing is a **later** option (not v1).
11. **Data = 30 trading sessions of 1-minute candles**, fetched from Definedge and loaded into ClickHouse for retrieval. Read path: ClickHouse first, write-through fetch from Definedge on cache miss.
12. **Placement**: signal-detail page, with a **per-panel chart-type toggle** (Candlestick / Renko / P&F), **P&F being the default**. Toggle applies to underlying future + each leg + combined.
13. **Candlesticks migrate to openalgo-charts** in this effort (the hand-rolled SVG `CandlestickChart` is retired for the signal-detail page).

---

## 3. Workstream 1 — Signal history & decision points

### 3.1 Current state (why this is needed)

- `run_screener_job()` (`backend/app/workers/screener_loop.py`) keeps the latest signals **only in memory** (`_latest_signals`).
- It also inserts one opaque `alerts` row (`alert_type='screener_signal'`, `payload_json`) per run.
- Rejected signals are **silently dropped** — `generate_credit_spread_signals` returns `[]` on every gate failure, and `run_screener` drops invariant failures with only a log line. No rejection reason or decision data is persisted.
- The "decision points" (IV rank, POP, credit, max loss, RSI, support/resistance, delta, breakevens, and the active `ScreenerParams`) are computed but never stored; they only survive inside the `reason` string.

### 3.2 New SQLite tables

Add to `backend/app/db/sqlite.py::SCHEMA` (and bump `SCHEMA_VERSION`).

```sql
-- One row per run_screener_job() invocation, including empty/degraded runs.
CREATE TABLE IF NOT EXISTS screener_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL UNIQUE,        -- uuid
    started_at      TEXT NOT NULL,               -- ISO 8601 UTC
    data_source     TEXT,                         -- live | clickhouse | sample | NULL (degraded)
    status          TEXT NOT NULL,                -- ok | sample | degraded
    message         TEXT,                         -- e.g. "3 signals from live data"
    signal_count    INTEGER NOT NULL DEFAULT 0,   -- accepted signals only
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per signal (accepted) or per rejected candidate.
CREATE TABLE IF NOT EXISTS screener_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES screener_runs(run_id),
    signal_id       TEXT,                         -- accepted ScreenerSignal.signal_id; NULL for rejected
    strategy_type   TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    expiry          TEXT,
    status          TEXT NOT NULL CHECK (status IN ('accepted','rejected')),
    reject_reason   TEXT,                         -- enum below; NULL when accepted
    generated_at    TEXT NOT NULL,
    data_source     TEXT,
    reason          TEXT,                         -- template/LLM reason (accepted only)
    reason_source   TEXT,                         -- template | llm | NULL
    signal_json     TEXT,                         -- full ScreenerSignal.model_dump(mode="json") (accepted only)
    decision_json   TEXT NOT NULL,                -- params + computed decision points (see §3.3)
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_screener_signals_run ON screener_signals(run_id);
CREATE INDEX IF NOT EXISTS idx_screener_signals_created ON screener_signals(created_at);
```

**`reject_reason` enum** (exhaustive, mapped from the engine gates):

| Value | Trigger in engine |
|---|---|
| `no_short_strike` | `_find_strike` returned `None` |
| `no_long_strike` | `_find_by_strike` for the long wing returned `None` |
| `iv_rank_below_min` | IV history available and `iv_rank < min_iv_rank` |
| `pop_below_min` | `pop < min_pop` |
| `credit_non_positive` | `credit <= 0` |
| `invariant_failed` | `invariants.check_signal` returned problems |
| `wing_missing` | (iron condor) put-side or call-side produced no signal |

### 3.3 `decision_json` shape

Captured for **every** row (accepted and rejected) so the history page can show the decision inputs, not just the outcome.

```json
{
  "params": {
    "min_iv_rank": 40.0,
    "min_pop": 0.60,
    "min_short_delta": 0.10,
    "max_short_delta": 0.30,
    "spread_width": 100.0
  },
  "computed": {
    "iv_rank": null,               // null => "unavailable, filter skipped"
    "pop": 0.72,
    "short_delta": 0.20,
    "credit": 25.0,
    "max_loss": 75.0,
    "rsi": 54.2,
    "support": 21600,
    "resistance": 22000,
    "breakeven": 21775.0,
    "breakeven_lower": null,
    "net_delta": -0.03,
    "net_theta": 0.9,
    "net_vega": -0.4,
    "net_gamma": 0.001
  },
  "reject_reason": null             // populated only for rejected rows
}
```

This is the same fact set `reasoning/generator.py::build_facts` already computes, extended with the params. A single helper produces `decision_json` for both the reason path and the persistence path so they can never diverge.

### 3.4 Engine refactor (capture rejects)

The generation functions currently return `List[ScreenerSignal]` and swallow failures. Refactor to return a structured result:

- New dataclass `RejectedSignal` (`strategy_type`, `symbol`, `expiry`, `reject_reason`, `decision_json`-partial, `generated_at`, `data_source`).
- New dataclass `ScreenerRunResult` (`accepted: List[ScreenerSignal]`, `rejected: List[RejectedSignal]`, `params: ScreenerParams`).
- `run_screener()` returns `ScreenerRunResult` instead of `List[ScreenerSignal]`.
- Each gate records `reject_reason` + the `computed` values available *at that point* before returning `[]`.

**No behavior change**: the set of *accepted* signals must be identical to today. Rejects are newly *observed*, not newly *filtered*. Existing unit tests (`tests/unit/test_engine.py`) must still pass unchanged (plus new reject-capture tests).

### 3.5 Persistence in `run_screener_job()`

- Create a `screener_runs` row (`run_id`) at the **start** of every run — including the degraded early-return branch (`snapshot is None` and sample disabled), so empty runs are visible.
- After `run_screener()` + `enrich_reasons()`, insert accepted and rejected rows into `screener_signals` referencing that `run_id`.
- Keep the existing `alerts` insert for backward compatibility (the dashboard `AlertsList` still reads `/api/v1/alerts`). Do not remove it in this workstream.

### 3.6 Retention (1 year, admin-gated)

- Config: `signal_history_retention_days: int = 365` in `backend/app/config.py`.
- **No scheduled prune job.** (Unlike `_retention_job`, which purges broker sessions.)
- Manual admin endpoint: `POST /api/v1/admin/history/prune-signals` with body `{ confirm: bool, dry_run: bool = true }`.
  - `dry_run=true` → returns the count of `screener_signals` + `screener_runs` rows older than retention, deletes nothing.
  - `dry_run=false, confirm=true` → deletes them; refuses if `confirm != true`.
  - This is the "seek admin approval" gate: the action is explicit and confirmed, never automatic.

### 3.7 Backend API

- `GET /api/v1/screener/runs` — paginated list of runs (desc), each with `run_id`, `started_at`, `status`, `data_source`, `message`, `signal_count`.
- `GET /api/v1/screener/runs/{run_id}` — the signals (accepted + rejected) for one run.
- `GET /api/v1/screener/history` — paginated flat list of signals (accepted + rejected) with filters: `strategy_type`, `symbol`, `status`, `date range`, `reason_source`. List rows omit the heavy `signal_json`; they carry a compact digest + `decision_json`.
- `GET /api/v1/screener/history/{id}` — full detail: `signal_json` (if accepted) + `decision_json` + `reason` + `reason_source` + `reject_reason`.

### 3.8 Frontend

- New `frontend/app/history/page.tsx` + `frontend/components/SignalHistoryPage.tsx`.
- `TopBar.tsx`: add a `History` link next to `Admin`.
- `lib/api.ts`: `fetchScreenerRuns`, `fetchRunDetail`, `fetchSignalHistory`, `fetchSignalHistoryDetail`.
- `lib/types.ts`: `ScreenerRun`, `SignalHistoryRow`, `SignalHistoryDetail`.
- History page UI:
  - Top: run list grouped by `run_id` (time, status badge, signal count, "nothing found this run" for zero-signal runs).
  - Drill-down: full signal (legs/greeks/RR), `reason` + `reason_source` badge, and a **decision-points panel** rendering `decision_json` (params vs. computed values; for rejected rows, show `reject_reason` prominently).

---

## 4. Workstream 2 — Signal-detail charts (frontend only)

Changes confined to `frontend/components/ContractDetailView.tsx` (+ its deps). No backend changes.

1. **Render all legs dynamically.** Replace the hardcoded `legs[0]` / `legs[1]` blocks with a `map` over `charts.series.legs`. Grid is responsive to panel count:
   - 2 legs → 4 panels (future + 2 legs + combined).
   - 4 legs (iron condor) → 6 panels (future + 4 legs + combined).
2. **Default interval = 5 minutes.** `useState<ChartInterval>(1)` → `useState<ChartInterval>(5)`. The interval selector already exists and stays.
3. **Add payoff panel.** Reuse `PayoffChart` + `fetchPayoff` (already exist; currently only on the dashboard). Wire a payoff section into `ContractDetailView` for the selected signal.

> Note: workstream 3 (below) will *replace* the hand-rolled `CandlestickChart` used here with an openalgo-charts renderer. To avoid rework, workstream 2's "render all legs dynamically" should be structured so the per-panel component is swappable — i.e., introduce a small `SignalChartPanel` wrapper now, and swap its internals in workstream 3.

---

## 5. Workstream 3 — P&F / Renko via openalgo-charts

### 5.1 Library decision

**`openalgo-charts`** (`https://github.com/marketcalls/openalgo-charts`, npm `openalgo-charts`).

- Apache-2.0, zero runtime deps, TypeScript, React/Next.js integration documented, `Asia/Kolkata` timezone support.
- Native P&F renderer (`point-figure` series) and Renko transforms, plus candlestick.
- Tiered bundles: import only `openalgo-charts` (base) + `openalgo-charts/transform` (registers the P&F renderer).

### 5.2 Rendering architecture

| Chart type | Computation | Renderer |
|---|---|---|
| Candlestick | none (raw OHLC) | openalgo-charts `addSeries('candlestick')` |
| Renko | **Python** `build_renko()` | openalgo-charts renders bricks as candles |
| P&F | **Python** `build_point_and_figure()` | openalgo-charts `addSeries('point-figure')` |

The frontend feeds pre-computed bricks/columns; openalgo-charts is a renderer only (consistent with the "Python computes, JS renders" philosophy).

### 5.3 Backend pure functions

New module `backend/app/charts/transforms.py` (or `app/screener/renko_pnf.py` — decide at implementation).

```python
def build_renko(candles: List[Candle], box_size: float, reversal: int) -> List[dict]:
    """Fixed box-size Renko from 1-min OHLC. `reversal` = number of boxes the
    price must retrace (in the opposite direction) before a reversal brick
    forms. Returns bricks in a candle-like shape (ts/open/high/low/close)."""

def build_point_and_figure(candles: List[Candle], box_size: float, reversal: int) -> List[dict]:
    """Classic 3-box (reversal-configurable) P&F from 1-min OHLC. Returns
    columns (X/O) with their box size, matching openalgo-charts' point-figure
    series contract."""
```

**Box size derivation** (per §2.10): `box_size = (pct / 100) * reference_price`, where `pct ∈ {0.1, 0.25, 0.5, 1, 3}` and `reference_price` is the series' own current price. Default `pct = 0.1` for all series except the combined-premium series (`pct = 0.5`). Computed **backend-side** so it is auditable and testable, then passed to the functions.

### 5.4 Data pipeline (30 sessions of 1-min)

- New module `backend/app/integrations/chart_history.py` (reuses `history_downloader` primitives where possible).
- **Fetch**: for each contract (underlying future + each leg), one `DefinedgeClient.get_historical_candles(exchange, token, "minute", start, end)` call with `start = now - 45 calendar days` (≈30 trading sessions incl. weekends/holidays). Single-call-per-contract is confirmed viable (the history probe showed ranges up to 730 days work in one call).
- **Store**: write into existing ClickHouse tables:
  - options legs → `options_1min_candles`.
  - future → `underlying_1min_candles` (`instrument_type='FUTURE'`).
  - Insert is idempotent (`ReplacingMergeTree`); re-running upserts safely.
- **Source tag**: reuse an existing `source` enum value (`backfill`) to avoid an `Enum8` migration, **or** add `'chart_history'` via `ALTER TABLE ... MODIFY COLUMN source ...`. Decide at implementation (lean toward reusing `backfill`).
- **Read path** (the charts/transforms endpoint): query ClickHouse for the last 30 sessions; on empty/missing, fetch from Definedge and store (write-through), then serve.

### 5.5 Backend endpoint

`POST /api/v1/charts/transforms` (new) — accepts the signal's contracts + transform params, returns per-series data:

```json
{
  "as_of": "…",
  "box_size": 115.0,
  "reversal": 3,
  "series": {
    "future":  { "candles": [...], "renko": [...], "pnf": [...] },
    "legs":    [ { "candles": [...], "renko": [...], "pnf": [...] } ],
    "combined":{ "candles": [...], "renko": [...], "pnf": [...] }
  }
}
```

- `candles` = raw OHLC (1-min, resampled to the selected candlestick interval).
- `renko` = `build_renko(...)` output.
- `pnf` = `build_point_and_figure(...)` output.
- `box_size` / `reversal` come from the request (overridable); backend computes the default box from the reference price when omitted.

### 5.6 Frontend (openalgo-charts)

- Add deps: `openalgo-charts` and `openalgo-charts/transform`.
- New components in `frontend/components/charts/`:
  - `OpenAlgoCandlestick.tsx` (replaces `CandlestickChart.tsx` on the signal-detail page).
  - `OpenAlgoRenko.tsx` (bricks → candlestick series).
  - `OpenAlgoPointFigure.tsx` (columns → `point-figure` series).
  - A `SignalChartPanel.tsx` wrapper holding the **per-panel toggle** (Candlestick / Renko / P&F, **P&F default**) + box/reversal controls. Box-size control exposes the presets `0.1% / 0.25% / 0.5% / 1% / 3%`, defaulting to `0.1%` (combined premium defaults to `0.5%`).
- `ContractDetailView` uses `SignalChartPanel` for the future + each leg + combined.
- `lib/api.ts`: `fetchChartTransforms(...)`; `lib/types.ts`: transform response types.
- `lib/charts.ts`: add transform series types + the chart-type union.

### 5.7 Data-contract verification (during implementation, not blocked)

Pin down the exact data shapes openalgo-charts expects, from its docs/type defs:
- Candlestick series: `{ time (UTC seconds), open, high, low, close }`.
- `point-figure` series: columns "carrying their own boxSize" — match the exact field names.
- Renko-as-candles: bricks must be candle-shaped (`time/open/high/low/close`).
This is the one place where the Python output shape must match a third-party JS contract — verify against openalgo-charts' `point-figure` series type before finalizing `build_point_and_figure`'s return shape.

---

## 6. Testing plan

### Backend

- `tests/unit/test_renko_pnf.py` — pure-function tests + **property tests**:
  - Renko: a brick's high/low differ by ≥ box_size; consecutive same-direction bricks don't double-count; reversal respects `reversal × box`.
  - P&F: a new column requires a move ≥ `reversal × box` against the prior column; column alternation X/O; box/column alignment.
- `tests/unit/test_engine_rejects.py` — each gate produces the correct `reject_reason` + non-empty `computed`.
- `tests/unit/test_signal_history.py` — run persistence (run_id grouping), empty-run rows, `decision_json` shape, retention dry-run/confirm semantics.
- `tests/api/test_signal_history.py` — endpoints: pagination, filters, 404 on unknown id, admin prune gating.
- `tests/api/test_charts_transforms.py` — transforms endpoint shape, box defaulting, 400 on bad params.

### Frontend

- Extend `frontend/tests/` with component-level tests for the dynamic leg grid, the chart-type toggle defaulting to P&F, and the history page's "nothing found this run" rendering.

### No regressions

- Existing `tests/unit/test_engine.py` must pass unchanged (accepted-signal set is identical).
- `ruff` + `pytest` + `npm run typecheck` + `npm test` all green before each workstream is considered done.

---

## 7. Implementation sequence (reviewed and shipped one at a time)

Each step is a commit/PR on `feature-charts`:

1. **WS1 backend — schema + reject capture + persistence** (`db/sqlite.py`, `screener/engine.py`, `workers/screener_loop.py`, `config.py`) + tests. ✅ Done (`SCHEMA_VERSION=4`; `RejectedSignal`/`ScreenerRunResult`/`build_decision`/`run_screener_with_rejects`; `_persist_history`; `signal_history_retention_days=365`; `tests/unit/test_engine_rejects.py`, `tests/unit/test_signal_history.py`).
2. **WS1 backend — history endpoints** (`api/routes/screener.py` + `admin.py`) + tests. ✅ Done (`GET /screener/runs`, `GET /screener/runs/{run_id}`, `GET /screener/history`, `GET /screener/history/{id}`, `POST /admin/history/prune-signals`; models in `schemas.py`).
3. **WS1 frontend — `/history` page + TopBar link** + types/api + tests. ✅ Done (`lib/types.ts` + `lib/api.ts` additions; `components/SignalHistoryPage.tsx` with Runs/Signals tabs + decision metrics; `app/history/page.tsx`; TopBar "History" link). typecheck/lint/build/vitest all green.
4. **WS2 frontend — dynamic legs + 5-min default + payoff panel** (`ContractDetailView` + `SignalChartPanel` wrapper). ✅ Done (`SignalChartPanel.tsx` swappable wrapper; `SignalPayoffPanel.tsx` reuses `PayoffChart`+`fetchPayoff`; dynamic `map` over `series.legs` → 4 or 6 panels; interval default 1→5 min). typecheck/lint/build/vitest all green.
5. **WS3 backend — `build_renko` / `build_point_and_figure`** + property tests. ✅ Done (`backend/app/charts/transforms.py`; `to_utc_seconds`/`box_size_from_pct`/`build_renko`/`build_point_and_figure`; `tests/unit/test_renko_pnf.py` 13 tests + `tests/property/test_renko_pnf_props.py` 8 hypothesis invariants; ruff clean; 181 passed).
6. **WS3 backend — chart-history pipeline + `/charts/transforms` endpoint** + tests. ✅ Done (`backend/app/integrations/chart_history.py` write-through cache — read ClickHouse, fetch+store on 45-day miss, `source='backfill'`; `POST /api/v1/charts/transforms` in `charts.py` with `_combine_candles`/`_transform_candles`/`_build_series` helpers; `tests/unit/test_chart_history.py` 10 + `tests/api/test_charts_transforms.py` 7; ruff clean; 198 passed).
7. **WS3 frontend — openalgo-charts renderers + per-panel toggle + candlestick migration** + tests. ✅ Done (`frontend/lib/charts.ts` transform types + `toUtcSeconds`/`toOpenAlgoBars` (IST→UTC edge conversion) + `CHART_TYPES`/`BOX_PRESETS`/defaults; `lib/api.ts` `fetchChartTransforms`; `components/charts/OpenAlgoChart.tsx` single renderer (candlestick + Renko both render as `'candlestick'`, P&F as `'point-figure'` via `openalgo-charts/transform`); `SignalChartPanel.tsx` per-panel Candles/Renko/P&F toggle defaulting to P&F; `ContractDetailView` box/reversal/interval fetch-level controls, switched to `/charts/transforms`; retired hand-rolled `CandlestickChart.tsx`; `tests/charts.test.ts` 8 tests). typecheck/lint/vitest(26)/build all green.
8. **End-to-end verification + `FEATURE_CHARTS_PLAN.md` updated to "implemented".**

---

## 8. Resolved definitions (confirmed by user)

All previously-open items are now resolved:

1. **Renko `reversal = 3`** → price must retrace `3 × box` before a reversal brick forms. ✅ Confirmed.
2. **P&F/Renko base is fixed 1-minute**, independent of the candlestick interval selector (default 5-min). ✅ Confirmed. Box presets `{0.1, 0.25, 0.5, 1, 3}%`, default `0.1%` (combined premium default `0.5%`).
3. **ClickHouse `source` tag** → reuse `backfill` (no migration). ✅ Confirmed.
4. **Combined-premium series** → existing `_combine_series` OHLC approximation as P&F/Renko input. ✅ Confirmed.
5. **Degraded runs** (no data) recorded as `status='degraded'`, `signal_count=0`. ✅ Confirmed.

---

## 9. Risks & rollback

- **Engine refactor (WS1)** is the riskiest change (touches the money-adjacent signal path). Mitigation: `ScreenerRunResult` preserves the exact accepted set; existing engine tests must pass unchanged. Rollback = revert the refactor, keep the new tables/endpoints (they'd just be empty).
- **ClickHouse writes (WS3)** reuse `ReplacingMergeTree` idempotency; no destructive ops. `ALTER ... MODIFY COLUMN source` (if chosen) is the only DDL risk — avoidable by reusing `backfill`.
- **openalgo-charts data contract (WS3)** is the main external unknown; contained to `build_point_and_figure`'s return shape and verified early (step 5) before any frontend work.
- No order-placement / money-path behavior is altered anywhere in this plan.
