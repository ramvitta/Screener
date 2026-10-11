# Options Screening & Monitoring Platform — SRS & Technical Architecture

## 1. Executive Summary & Tech Stack Blueprint

### 1.1 Purpose
A read-only intelligence platform for screening options strategies, monitoring open positions, and tracking performance. **No automated order execution** — the system surfaces trade ideas and alerts; execution stays manual via the broker's own terminal/app.

### 1.2 Tech Stack

| Layer | Technology | Role |
|---|---|---|
| Backend | Python 3.11+, FastAPI | Async REST + WebSocket API, quantitative processing |
| Time-series storage | ClickHouse | 1-min candles/ticks/depth for options, historical backfill |
| App storage | SQLite | Settings, session tokens, screener rules, alerts, position logs, journal |
| Broker integration | Definedge Securities (REST + WebSocket) | Live data, historical data, account/positions |
| Frontend | React / Next.js | Dashboard UI, WebSocket live updates |
| Background jobs | asyncio background tasks + APScheduler | Ingestion, backfill, screener loops |

### 1.3 Phase 1 Scope (this document's build target)
Phase 1 deliberately narrows the full vision (15 strategies × 4 indices) to one working end-to-end slice:

- **Instrument universe**: Nifty index options only (all active weekly/monthly expiries).
- **Strategies**: 2 only —
  1. **Credit Spreads** (Bull Put Spread, Bear Call Spread)
  2. **Iron Condor**
- **Data**: live 1-min ingestion **and** historical 1-min backfill for Nifty option contracts, as far back as Definedge's historical API exposes.
- **Execution**: manual (read-only alerts/dashboard only).
- **Explicitly deferred to later phases**: remaining 13 strategies, BankNifty/Sensex/Liquid 50, any second broker (e.g. Kotak Securities), automated order placement.

This scope exists so the architecture below (data pipeline, schemas, engine) is designed to generalize to the full 15-strategy/4-index vision, but Phase 1 build effort targets only the slice above.

---

## 2. Definedge Integration & Market Data Ingestion Pipeline

> **Source**: verified against `pyintegrate` (github.com/Definedge-Securities/pyintegrate, `main` branch) — the official Python SDK — since the hosted API doc page at definedgesecurities.com/api-documentation/ doesn't expose raw endpoint paths.

### 2.1 Authentication
Definedge Integrate uses a two-step login against a dedicated auth host, separate from both the trading REST API and the WebSocket:

1. On startup, FastAPI reads `DEFINEDGE_API_TOKEN` / `DEFINEDGE_API_SECRET` from environment.
2. `GET https://signin.definedgesecurities.com/auth/realms/debroking/dsbpkc/login/{api_token}` with header `api_secret: <secret>` → `{"otp_token": ...}`.
3. User supplies the OTP/TOTP. Compute `ac = sha256(otp_token + otp + api_secret)`.
4. `POST https://signin.definedgesecurities.com/auth/realms/debroking/dsbpkc/token` with JSON `{"otp_token", "otp", "ac"}` → returns **four** session values: `uid`, `actid`, `api_session_key`, `susertoken`.
   - `api_session_key` authenticates REST calls (`Authorization` header).
   - `susertoken` is the **WebSocket** session key — distinct from `api_session_key`, used only in the WS login frame.
5. Store `uid`, `actid`, `api_session_key`, `ws_session_key` (`= susertoken`), `expiry_time` in SQLite `broker_sessions` table (see §3.2).
6. A background task refreshes/re-authenticates before token expiry and on 401 responses.

**TOTP auto-login (bypasses manual OTP)**: if `DEFINEDGE_TOTP_SECRET` is set (an External TOTP secret enabled at https://myaccount.definedgesecurities.com/security), `POST /api/v1/auth/definedge/login/auto` runs the full two-step flow server-side — generating the 6-digit code with `pyotp.TOTP(secret).now()` instead of prompting for a manually-entered OTP — and stores the resulting session directly. The manual `/login` + `/verify` pair remains available as a fallback when no TOTP secret is configured.

### 2.2 REST Endpoints Consumed

Endpoints span three separate hosts — there is no single API base URL.

| Host | Endpoint | Purpose | Consumed by |
|---|---|---|---|
| `app.definedgesecurities.com` | `GET /public/allmaster.zip` (static, unauthenticated) | Instrument master — zip containing `allmaster.csv`; resolves tradable Nifty option contracts per expiry | Backfill job, contract resolver |
| `integrate.definedgesecurities.com/dart/v1` | `GET /quotes/{exchange}/{token}` | Current LTP, bid/ask snapshot (by instrument token, not trading symbol) | Screener, position monitor |
| `data.definedgesecurities.com/sds/history` | `GET /{exchange}/{token}/{timeframe}/{start}/{end}` (`timeframe` ∈ `minute`\|`day`\|`tick`; dates as `ddmmyyyyHHMM`) | 1-min OHLCV backfill per contract | Historical backfill job |
| `integrate.definedgesecurities.com/dart/v1` | `GET /positions` | Live open positions | Position monitoring dashboard |
| `integrate.definedgesecurities.com/dart/v1` | `GET /limits` | Available margin/balance | Dashboard header |
| `integrate.definedgesecurities.com/dart/v1` | `GET /holdings` | Holdings (for covered strategies later) | Deferred (Phase 2) |

Not used by this platform (read-only scope) but real and available on the same host: `placeorder`, `modify`, `cancel/{order_id}`, `sliceorder`, `productconversion`, `orders`, `order/{order_id}`, `trades`, `margin`, `spancalculator`, and GTT/OCO order variants.

### 2.3 WebSocket Streaming
Definedge's WebSocket (`wss://trade.definedgesecurities.com/NorenWSTRTP/`) delivers tick-level LTP, depth, and account order updates over a single connection, multiplexed by message type.

- **Login** (required immediately after connect, using `ws_session_key`/`susertoken` from §2.1 — not `api_session_key`):
  `{"t":"c","uid":<uid>,"actid":<actid>,"source":"TRTP","susertoken":<ws_session_key>}`
- **Subscribe** — one frame per channel type, keyed by `t`:
  - Tick (`t:"t"`): `{"t":"t","k":"EXCH|TOKEN#EXCH|TOKEN..."}`
  - Depth (`t:"d"`): `{"t":"d","k":"EXCH|TOKEN#..."}`
  - Order updates (`t:"o"`): `{"t":"o","actid":<actid>}` — account-wide, no per-token key; useful once execution/fill logging is added.
  - Unsubscribe uses `t` values `"u"`, `"ud"`, `"uo"` respectively.
- FastAPI ingest worker maintains one persistent WS connection per session, subscribing to the token list resolved for active Nifty option contracts (current + next expiry, strikes within a configurable moneyness band, e.g. ±15% of spot).
- Ticks are buffered in-memory and rolled into 1-minute OHLCV candles (aligned to minute boundaries) before being flushed to ClickHouse in batches (reduces per-tick insert overhead — ClickHouse is optimized for batch inserts, not single-row writes).

### 2.4 Two Ingestion Paths

**A. Live ingestion (streaming)**
```
Definedge WebSocket (ticks)
   → FastAPI ingest worker (in-memory 1-min OHLCV aggregation)
   → batched INSERT → ClickHouse.options_1min_candles
   → session/heartbeat state → SQLite.broker_sessions
```

**B. Historical backfill (batch, one-time + incremental)**
```
Definedge historical REST API (per contract, per date range)
   → FastAPI backfill worker (paginated, rate-limited)
   → batched INSERT → ClickHouse.options_1min_candles
   → progress checkpoint → SQLite.backfill_progress
```

- **One-time backfill**: for every Nifty option contract resolvable from the instrument master (going back as far as Definedge's historical API allows — typically limited by contract lifetime since options expire, so backfill scope is "full history of every expired+current contract still queryable"), pull 1-min candles and load into ClickHouse.
- **Incremental backfill**: nightly job re-checks `backfill_progress` per contract and fills any gaps (missed ticks during disconnects, days the WS worker was down).
- **Rate limiting & resumability**: backfill worker respects Definedge's rate limits (configurable requests/sec), and persists `(contract_id, last_completed_date)` in `backfill_progress` so an interrupted backfill resumes rather than restarting.
- New contracts appear every expiry cycle (weekly for Nifty) — the backfill job runs as part of the nightly scheduler to pick up newly listed strikes/expiries automatically.

---

## 3. Database Schemas

### 3.1 ClickHouse — `options_1min_candles`

```sql
CREATE TABLE options_1min_candles
(
    symbol          LowCardinality(String),   -- e.g. 'NIFTY'
    expiry          Date,
    strike          Decimal(10, 2),
    option_type     Enum8('CE' = 1, 'PE' = 2),
    ts              DateTime('Asia/Kolkata'), -- candle open time, 1-min aligned
    open            Decimal(10, 2),
    high            Decimal(10, 2),
    low             Decimal(10, 2),
    close           Decimal(10, 2),
    volume          UInt64,
    oi               UInt64,
    oi_change        Int64,
    source          Enum8('live' = 1, 'backfill' = 2)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (symbol, expiry, strike, option_type, ts)
TTL ts + INTERVAL 5 YEAR   -- adjust/remove once retention policy is decided
SETTINGS index_granularity = 8192;
```

Design notes:
- `ORDER BY (symbol, expiry, strike, option_type, ts)` matches the screener's typical query pattern (fetch a contract's time series, or scan all strikes for one expiry) and gives ClickHouse's sparse primary index an efficient skip pattern.
- Monthly partitioning (`toYYYYMM`) keeps individual parts manageable and lets old partitions be dropped cheaply if retention policy changes.
- `source` distinguishes live-streamed rows from backfilled rows for debugging/data-quality checks; `ReplacingMergeTree` can be substituted later if backfill/live overlap produces duplicate rows that need de-duplication (recommended once both paths are live — see Phase 2 note in §5).

Optional companion table for market depth (top-5 bid/ask), same partition/order keys, deferred unless Phase 1 needs it for POP/liquidity checks:

```sql
CREATE TABLE options_market_depth
(
    symbol       LowCardinality(String),
    expiry       Date,
    strike       Decimal(10, 2),
    option_type  Enum8('CE' = 1, 'PE' = 2),
    ts           DateTime('Asia/Kolkata'),
    bid_price    Array(Decimal(10,2)),
    bid_qty      Array(UInt64),
    ask_price    Array(Decimal(10,2)),
    ask_qty      Array(UInt64)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(ts)
ORDER BY (symbol, expiry, strike, option_type, ts);
```

### 3.2 SQLite — Application Database

```sql
-- App-wide configuration (key/value)
CREATE TABLE app_settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Definedge session/token management
-- api_session_key authenticates REST calls; ws_session_key (the login
-- response's `susertoken` field) authenticates the WebSocket login frame —
-- the two are distinct values, see §2.1.
CREATE TABLE broker_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    broker          TEXT NOT NULL DEFAULT 'definedge',
    uid             TEXT NOT NULL,
    actid           TEXT NOT NULL,
    api_session_key TEXT NOT NULL,
    ws_session_key  TEXT NOT NULL,
    issued_at       TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1
);

-- Backfill progress checkpoint, per contract
CREATE TABLE backfill_progress (
    contract_id         TEXT PRIMARY KEY,   -- e.g. 'NIFTY|2026-01-29|22000|CE'
    symbol              TEXT NOT NULL,
    expiry              TEXT NOT NULL,
    strike              REAL NOT NULL,
    option_type         TEXT NOT NULL CHECK (option_type IN ('CE','PE')),
    last_completed_date TEXT,               -- last date successfully backfilled
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','in_progress','complete','failed')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Screener rule definitions (per strategy, user-tunable thresholds)
CREATE TABLE screener_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_type   TEXT NOT NULL CHECK (strategy_type IN ('bull_put_spread','bear_call_spread','iron_condor')),
    symbol          TEXT NOT NULL DEFAULT 'NIFTY',
    is_active       INTEGER NOT NULL DEFAULT 1,
    params_json     TEXT NOT NULL,   -- e.g. {"min_iv_rank":40,"min_pop":0.65,"max_delta":0.2}
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Alerts generated by the screener / position monitor
CREATE TABLE alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type      TEXT NOT NULL CHECK (alert_type IN ('screener_signal','profit_target','max_loss','margin_call')),
    strategy_type   TEXT,
    symbol          TEXT NOT NULL,
    payload_json    TEXT NOT NULL,   -- full structured detail (legs, strikes, greeks, reason)
    is_read         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Tracked/open positions (manually confirmed by user after screener signal)
CREATE TABLE position_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_type   TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    expiry          TEXT NOT NULL,
    legs_json       TEXT NOT NULL,   -- [{strike, option_type, action, qty, entry_price}, ...]
    entry_time      TEXT NOT NULL,
    exit_time       TEXT,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    profit_target   REAL,
    max_loss        REAL,
    realized_pnl    REAL,
    notes           TEXT
);

-- Trade journal (post-trade notes/review, linked to position_logs)
CREATE TABLE trade_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER NOT NULL REFERENCES position_logs(id),
    entry_rationale TEXT,
    exit_rationale  TEXT,
    lessons_learned TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
```

---

## 4. Screener Engine Architecture (Phase 1 Slice)

### 4.1 Factor Calculation Pipeline

For each active Nifty expiry, the screener runs on a schedule (e.g. every 5 min during market hours):

1. **Fetch inputs**: latest 1-min candles + current LTP/depth per strike (ClickHouse), spot price and recent history (for RSI/MA/S-R).
2. **Volatility factors**: IV per strike (from option price via Black-Scholes inversion), IV Rank / IV Percentile (computed against trailing IV history — this is where historical backfill data becomes essential, since IV Rank needs a lookback window, e.g. 1 year).
3. **Greeks**: Delta, Gamma, Theta, Vega per leg (Black-Scholes / Black-76 model using spot, strike, IV, time-to-expiry, risk-free rate).
4. **Technicals**: RSI(14), moving averages (20/50 EMA), support/resistance levels computed off underlying Nifty spot candles.
5. **Risk/Reward & POP**: for each candidate spread/condor combination, compute max profit, max loss, breakevens, and Probability of Profit (via delta approximation or lognormal price distribution at expiry).

### 4.2 Strategy-Specific Logic (Phase 1)

**Credit Spreads (Bull Put / Bear Call)**
- Candidate generation: for each expiry, scan OTM strikes within the configured delta band (e.g. short leg delta 0.15–0.30).
- Filter: min IV Rank, min credit received, min POP, max defined loss vs. account risk limit (from `screener_rules.params_json`).
- Rank candidates by risk-adjusted return (credit / max loss).

**Iron Condor**
- Candidate generation: pair a Bull Put Spread + Bear Call Spread around current spot, symmetric or skewed by delta.
- Filter/rank: combined POP, combined credit, and IV Rank as primary signal (condors favor high-IV, range-bound conditions — technicals (RSI/S-R) used to confirm range-bound bias).

### 4.3 "Reason for Trade" Generation
A lightweight rules-based rationale generator (Phase 1; not LLM-based) composes a templated sentence from the factor values that triggered the signal, e.g.:

> "IV Rank at 62% (elevated) with Nifty RSI at 48 (neutral, range-bound). Bull Put Spread 21800/21700 offers 68% POP with ₹35 credit against ₹65 max loss."

Template inputs: `strategy_type`, `iv_rank`, `pop`, `credit`, `max_loss`, `technical_bias` (from RSI/MA state), `key_level` (nearest support/resistance).

### 4.4 UI Payload Format
Screener results pushed to frontend via WebSocket (and available via REST for initial load):

```json
{
  "signal_id": "uuid",
  "strategy_type": "bull_put_spread",
  "symbol": "NIFTY",
  "expiry": "2026-01-29",
  "legs": [
    {"strike": 21800, "option_type": "PE", "action": "SELL", "delta": -0.22, "iv": 14.8},
    {"strike": 21700, "option_type": "PE", "action": "BUY", "delta": -0.12, "iv": 15.9}
  ],
  "greeks": {"delta": -0.10, "theta": 4.2, "vega": -1.8, "gamma": -0.003},
  "risk_reward": {"max_profit": 35, "max_loss": 65, "breakeven": 21765, "pop": 0.68},
  "reason": "IV Rank at 62% ...",
  "generated_at": "2026-01-15T10:35:00+05:30"
}
```

### 4.5 Background Worker Design
- **asyncio background tasks** (via FastAPI's `lifespan` + `asyncio.create_task`) for the WS ingest worker and 1-min candle flush loop — these are long-lived, I/O-bound, and benefit from sharing the event loop with the API server.
- **APScheduler** (in-process, `AsyncIOScheduler`) for periodic jobs with fixed cadence: screener run (every 5 min market hours), nightly historical backfill/gap-fill, session token refresh.
- Celery is **not** used in Phase 1 — the workload doesn't yet need multi-process/distributed workers or a message broker; APScheduler + asyncio tasks are sufficient for a single-instance deployment. Revisit if Phase 2 scale (4 indices × 15 strategies) requires horizontal scaling.

### 4.6 REST/WebSocket Endpoint List

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/auth/definedge/login` | Trigger Definedge login/OTP flow |
| `GET` | `/api/v1/screener/signals` | Latest screener signals (paginated) |
| `GET` | `/api/v1/screener/rules` | List screener rule configs |
| `POST` | `/api/v1/screener/rules` | Create/update a rule |
| `GET` | `/api/v1/positions` | Current tracked positions |
| `POST` | `/api/v1/positions` | Log a manually-executed position |
| `PATCH` | `/api/v1/positions/{id}` | Update/close a position |
| `GET` | `/api/v1/alerts` | Alert history |
| `GET` | `/api/v1/backfill/status` | Backfill progress per contract |
| `WS` | `/ws/quotes` | Live LTP/greeks stream for subscribed contracts |
| `WS` | `/ws/alerts` | Live push of new screener signals & position alerts |

---

## 5. Phase 1 Implementation Plan & Task Checklist

### Step 1 — Project scaffolding
- [ ] Initialize FastAPI project structure (`app/api`, `app/workers`, `app/models`, `app/db`)
- [ ] Set up ClickHouse instance (Docker) and SQLite file-based DB
- [ ] Apply schemas from §3.1 and §3.2

### Step 2 — Definedge integration
- [ ] Implement login/OTP/session flow (§2.1), persist to `broker_sessions`
- [ ] Implement instrument master fetch + Nifty option contract resolver
- [ ] Implement REST client wrappers (quotes, positions, margins, historical candles)

### Step 3 — Historical backfill
- [ ] Build backfill worker: iterate resolvable Nifty contracts, pull full available 1-min history, batch-insert into ClickHouse
- [ ] Implement `backfill_progress` checkpointing and resumability
- [ ] Schedule nightly incremental gap-fill job (APScheduler)

### Step 4 — Live ingestion
- [ ] Implement Definedge WebSocket client + reconnect/backoff logic
- [ ] Implement in-memory 1-min OHLCV aggregation + batched ClickHouse flush
- [ ] Verify live and backfilled data don't produce conflicting/duplicate rows (add dedup logic or switch to `ReplacingMergeTree` if needed)

### Step 5 — Screener engine (Phase 1 strategies only)
- [ ] Implement Greeks/IV calculation module (Black-Scholes)
- [ ] Implement IV Rank/Percentile calc using backfilled history
- [ ] Implement Credit Spread and Iron Condor candidate generation + ranking
- [ ] Implement "Reason for Trade" template generator
- [ ] Wire up APScheduler periodic screener run → `alerts` table → WS push

### Step 6 — Position monitoring
- [ ] Implement position CRUD endpoints (`position_logs`)
- [ ] Implement profit-target/max-loss monitor (compares live quotes to position params, emits alerts)

### Step 7 — Frontend (minimal)
- [ ] Next.js dashboard: screener signal feed, position tracker table, alert panel
- [ ] WebSocket client hooks for `/ws/quotes` and `/ws/alerts`

### Step 8 — End-to-end verification
- [ ] Confirm backfill populates ClickHouse for a sample of real Nifty contracts
- [ ] Confirm live WS ingestion produces continuous 1-min candles during market hours
- [ ] Confirm screener produces at least one signal with correct Greeks/POP/reason text
- [ ] Confirm position monitor fires an alert when a test position crosses its profit/loss threshold

### Deferred to Phase 2+
- Remaining 13 strategies (Long Call/Put, Covered Call/CSP, Debit Spreads, Butterflies, Calendars/Diagonals, Straddles/Strangles, Ratio Spreads, custom multi-leg)
- BankNifty, Sensex, Liquid 50 instrument universes
- Kotak Securities as a second/alternate broker adapter (broker-agnostic interface should be kept in mind but not built now)
- Automated order execution
- Celery/distributed worker scaling if load requires it
