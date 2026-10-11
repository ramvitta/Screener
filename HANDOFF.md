# Handoff — resume here tomorrow

Written 2026-10-02, end of session. All processes were stopped cleanly before
shutdown (`bash scripts/stop.sh --all` — backend, frontend, and ClickHouse all
stopped). Nothing is running right now; start with `bash scripts/start.sh`.

## ⚠️ Do this first: rotate Definedge credentials

A **parallel session** doing a security hardening pass found that the
Definedge API token had been written to `logs/backend.log` in plaintext
before redaction was added, and the TOTP secret sat in an unprotected
folder. **This is a manual action only you can do** — their plan
([IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), task T0.1) explicitly
lists it as "still required." Rotate `DEFINEDGE_API_TOKEN`,
`DEFINEDGE_API_SECRET`, and `DEFINEDGE_TOTP_SECRET` at
https://myaccount.definedgesecurities.com/security, update `backend/.env`,
and deactivate old rows in the `broker_sessions` table if any survive the
rotation.

## What this project is

FastAPI + Next.js options screener and position logger for a Definedge
brokerage account (NIFTY only, Phase 1 scope). Read-only by design — no
order placement, enforced by `backend/app/core/execution_guard.py` and a
CI test that fails if any order-placement method ever appears outside it.

## Two work threads landed in this repo

### 1. My thread: UI gap-closing (tabs, positions, chain, analytics)
Plan file: `~/.claude/plans/can-you-highlight-what-squishy-garden.md` (this
file's content has been overwritten a few times across sessions — treat the
summary below as the current source of truth, not that file).

**Done (Phases 1–5):**
- **Phase 1** — tab shell (Signals / Positions & PNL / Chain & Entry /
  Analytics / History & Backtest), live quote polling.
- **Phase 2** — real Positions & P&L grid: per-leg SL/TSL/target, roll-to,
  exit/exit-all, booked vs unbooked P&L. Backend: `position_legs` table,
  `backend/app/api/routes/positions.py`.
- **Phase 3** — payoff graph with target-date slider.
  `backend/app/api/routes/analytics.py` (`POST /payoff`),
  `frontend/components/PayoffPanel.tsx` +
  `frontend/components/charts/PayoffChart.tsx`. Works for both a live
  screener signal (prices it at today's theoretical premium) and a logged
  position (solves IV from its live ltp).
- **Phase 4** — option chain. `backend/app/api/routes/chain.py`
  (`GET /chain/{symbol}`), `frontend/components/OptionChainPanel.tsx`.
  "Log ATM Straddle" / "Log Strangle ±N" just log a position
  (`POST /positions`) — never place a broker order.
- **Phase 5** — OI/PCR + session IV + Kelly sizing, all in the Analytics
  tab. `backend/app/api/routes/analytics.py` (`GET /oi-pcr`, `GET /kelly`),
  `frontend/components/MarketAnalyticsPanel.tsx` +
  `frontend/components/charts/SessionIvChart.tsx`.
  **Important finding**: Definedge's live `/quotes` endpoint carries **no
  OI field at all** (confirmed against a real response) — OI only exists in
  the historical 1-min candle endpoint. The OI/PCR panel pulls the latest
  completed session's candles for this reason, which makes it noticeably
  slower (~4–8s for a full chain depth) than the live chain tab — that's
  expected, not a bug, and why it polls every 60s instead of every 5s.

**Pending — needs a decision before continuing:**
- **Phase 6 (history/backtest pages)**: blocked on ClickHouse having
  backfilled historical option data, which it doesn't yet. Building the
  pages now would mean either fabricating numbers (directly violates the
  hardening session's core "never present fabricated market data as a
  recommendation" principle — see F6/T3.3 in `IMPLEMENTATION_PLAN.md`) or
  shipping a page that only ever says "no data yet." **Options for
  tomorrow**: (a) build it now as an honest "insufficient data" state with
  real wiring ready for whenever backfill lands, (b) hold off until a
  backfill job exists, or (c) scope a minimal backfill job first (there's
  already a separate, fully-unbuilt plan for exactly this — see below).
- **Phase 7 (AI risk panels)**: the other session already built
  `backend/app/reasoning/` (LLM "Reason for Trade", off by default via
  `LLM_REASON_ENABLED`) with a full faithfulness/safety validator and eval
  framework. Any "AI risk analysis" panel should reuse that framework
  rather than bolt on a separate ad hoc LLM call — needs design discussion
  before building.

### 2. Parallel thread: security/guardrails hardening — **implemented through M4**
Plan file: `~/.claude/plans/build-a-plan-on-replicated-panda.md`. Status
doc: `IMPLEMENTATION_PLAN.md` at repo root (§8 has the full task-by-task
status table). Headline: bearer-token auth on every route + WebSocket, log
redaction, encrypted session storage, `data_source` labels on every signal,
sample-data refused unless `ALLOW_SAMPLE_DATA=1`, server-owned P&L with
atomic/idempotent exits, hash-chained audit log, iron-condor math bugs
fixed, 149 backend tests + 18 frontend tests (84% coverage), an eval suite
under `backend/evals/`. Explicitly deferred by the owner: encrypted DB
backups/journal export, mutation testing, calibration backtest (needs
ClickHouse history), Playwright e2e.

### 3. Untouched: Admin page for historical data download (F&O stocks)
Plan file: `~/.claude/plans/what-is-needed-in-rustling-curry.md`. **0%
built** — no `admin.py` route, no `history_downloader.py`, no `/admin`
page exist yet. This is the plan that would actually solve Phase 6's "no
ClickHouse backfill" blocker, if you want to prioritize it before Phase 6.

## Conventions to follow in any new backend code (set by the hardening pass)
- Every new router must be added to the `_protected` list in
  `backend/app/main.py` (applies the bearer-auth dependency).
- Use `app.api.routes.auth.ensure_authenticated()` before any Definedge
  call; it handles session restore/refresh/re-auth — never touch
  `_client._session` directly.
- Request bodies that shouldn't accept extra/server-owned fields: inherit
  `StrictModel` (schemas.py) — rejects unknown fields.
- Money/price fields: use the `Price` / `PositivePrice` / `Quantity`
  annotated types in `schemas.py` (bounds + NaN/Inf rejection), not bare
  `float`.
- Never log or return `str(exc)` from a broker/httpx exception — use
  `type(exc).__name__` (the redaction filter in `core/logging.py` can't
  scrub structure it's never shown). `DefinedgeAuthError` → `HTTPException`
  with a generic message, status 503 for "broker unreachable."
- Any signal/snapshot needs a `DataSource` (`live` / `clickhouse` /
  `sample`) — sample data is refused unless `ALLOW_SAMPLE_DATA=1`.
- Frontend: all `lib/api.ts` calls must go through `getJson`/`postJson`/
  `patchJson` (they attach the bearer token via `authHeaders()`) — never a
  raw `fetch`.
- React: this repo's ESLint enforces React Compiler purity rules — no
  `Date.now()`/`new Date()` directly in a render body (wrap in a util like
  `lib/format.ts`'s `daysToExpiry`, call it instead) and no synchronous
  `setState` at the top of a `useEffect` body (use a lazy `useState`
  initializer or derive values instead of mirroring them into state).

## How to resume
```bash
cd /Users/rampunamaraju/Option_Trades
bash scripts/start.sh            # ClickHouse (Docker) + backend :8000 + frontend :3001
```
Auth token for manual API calls: `backend/.env` → `API_AUTH_TOKEN`.
```bash
TOKEN=$(grep '^API_AUTH_TOKEN=' backend/.env | cut -d= -f2)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/health
```
Backend tests: `cd backend && source .venv/bin/activate && python -m pytest -q`
(149 tests, should be green). Frontend: `cd frontend && npm run lint &&
npx tsc --noEmit && npm run build`.

## Suggested next question to ask when resuming
"Rotate the Definedge credentials yet? And for Phase 6 — build the honest
'no data yet' version now, or do the admin historical-data-download page
first so Phase 6 has real backfilled data to show?"
