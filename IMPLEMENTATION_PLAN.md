# Implementation Plan: Guardrails, Evals and Testing — Option_Trades

Status: Implemented through M4 (see §8) · Date: 2026-10-01 · Owner: Ram Punamaraju

## 1. Context
Option_Trades is a FastAPI + Next.js options **screener and position logger** for a Definedge brokerage account. It has **no order placement code, no LLM usage, no tests and no CI**. It still holds live broker secrets and a broker session with trade authority, and its API has no authentication. The risk comes from three sources: credential exposure, unauthenticated access, and wrong numbers shown as signals that a user might trade on.

### Findings that drive priorities
| # | Finding | Location |
|---|---|---|
| F1 | Live API token, secret and **TOTP secret** in `.env`. No root `.gitignore`, and the root is not a git repo | `backend/.env` |
| F2 | API token leaked in the login URL in the log (httpx INFO logging) | `logs/backend.log:7`, `main.py:15`, `definedge_client.py:99-102` |
| F3 | Session keys stored in plaintext and never purged | `db/sqlite.py:15-25`, `auth.py:27-77` |
| F4 | No auth on any route or WebSocket, including relogin and margin | `api/routes/*`, `ws.py` |
| F5 | Hardcoded ClickHouse password, ports published on all interfaces | `docker-compose.yml:6-9` |
| F6 | Silent fallback to a hardcoded sample chain. Signals are not labelled. The IV-rank filter is effectively off | `screener_loop.py:38-60,138-139,169-176` |
| F7 | Iron condor POP is really `min(p1,p2)`, and only the call breakeven is reported | `engine.py:129,157` |
| F8 | No bounds on money fields. `PositionUpdate` can overwrite `realized_pnl` and `status`. `exit-all` is not atomic. Floats for money | `schemas.py:75-135`, `positions.py:25-27,149-155,229-230` |
| F9 | Many silent `except Exception` blocks | `market.py:40`, `chain.py:29`, `positions.py:266`, `screener_loop.py:119,149,164` |
| F10 | Unpinned dependencies, no lint, type-check, tests or CI | `requirements.txt` |
| F11 | Raw upstream error text returned (`str(exc)`), and `/login` and `/verify` return 500s on httpx errors | `auth.py:143-178` |
| F12 | Concurrent logins can revoke each other (shared `_client._session`) | `auth.py`, `definedge_client.py` |

## 2. Runtime vs development time
- **Guardrails = RUNTIME [RT].** Code in the app that blocks, redacts or degrades while it runs. Failure means a rejected request or a dropped signal.
- **Tests = DEV TIME [DEV].** Deterministic pass/fail in CI. They also prove the guardrails work.
- **Evals = DEV TIME [DEV]**, with optional runtime monitoring. Scored checks against golden datasets with thresholds and baselines. Failure means a regression gate.
- **[BOTH]** means one piece of code or logic is used in both modes.

| Concern | Runtime guardrail [RT] | Dev-time test or eval [DEV] |
|---|---|---|
| Secrets in logs | Redaction filter, httpx at WARNING, generic error bodies | Log-capture test, gitleaks pre-commit |
| Access | Auth dependency, rate limits, Origin check | API tests for 401, rate limit and CORS |
| Money inputs | Pydantic bounds, server-derived P&L, atomic idempotent exit, audit log | Property and API tests, atomicity test |
| Order safety | `TRADING_MODE=disabled`, execution guard, kill switch | CI test that fails if order methods appear outside the guard |
| Bad data | Refuse sample chain, stale-quote rejection, `data_source` label, degraded banner | Fault-injection tests |
| Signal quality | Per-signal invariants drop bad signals | Evals: golden pricing set, golden chains, calibration backtest |
| PII | Masking in API and logs, retention purge | Masking tests, data inventory review |
| Supply chain | Pinned deps | pip-audit, npm audit, Dependabot, CI |

**Shared code:** the signal invariants live once in `backend/app/screener/invariants.py`. They run at runtime as a guardrail and in `backend/evals/` as a scored check.

## 3. Milestones
| Milestone | Scope | Effort | Exit criteria |
|---|---|---|---|
| **M0 Containment** | T0.x | 1 day | Secrets rotated, scrubbed and gitignored, with no secret in logs or the DB |
| **M1 Secure foundation** | T1.x, T2.1–2.3 | 2–3 days | Every route returns 401 without auth, the redaction test passes, CI runs |
| **M2 Money safety** | T3.x, T2.4–2.5 | 3–4 days | Money-field validation, atomic exits, sample data refused, execution guard test passes |
| **M3 Correctness and evals** | T4.x, T2.6–2.7, T5.x | 3 days | Pricing and screener golden evals pass, with the iron condor bugs fixed |
| **M4 Hardening** | T6.x, T2.8–2.9 | 3 days | Contract, concurrency, resilience and frontend tests green, coverage gates met |
| **M5 Later** | T7.x | Ongoing | Retention, mutation testing, calibration monitoring, LLM evals if added |

## 4. Tasks

### M0 — Containment
- **T0.1 [manual]** Rotate the Definedge API token, secret and TOTP secret. Deactivate and delete all rows in `broker_sessions`. Scrub `logs/backend.log` and `logs/frontend.log`.
- **T0.2 [DEV]** Add root `.gitignore` (`.env*`, `*.db`, `logs/`, `.run/`, `Inspiration/`, `.venv`, `node_modules`), then `git init` at the root. Review the `Inspiration/` images for account data.
- **T0.3 [RT]** Move secrets to macOS Keychain via `keyring`, loaded in `backend/app/config.py`. Fall back to `.env` with `chmod 600` for dev. Update `.env.example`.
- **T0.4 [RT]** In `main.py`, set `httpx` and `httpcore` log levels to WARNING.
- **T0.5 [RT]** `docker-compose.yml`: bind to `127.0.0.1:8123` and `127.0.0.1:9000`, and read the ClickHouse password from the environment.
- **Done when:** `grep -r` for the old token, secret and TOTP across the project, logs and DB returns nothing.

### M1 — Secure foundation
- **T1.1 [RT] Log redaction.** New `backend/app/core/logging.py` with a `logging.Filter` that masks tokens, secrets, OTPs, session keys and `uid`/`actid` (keep the last 2 characters). Structured JSON logs, request IDs and rotation. Replace `basicConfig` at `main.py:15`.
- **T1.2 [RT] Safe errors.** Stop returning `str(exc)` (`auth.py:158`). Strip URLs from broker exceptions in `definedge_client.py`. Catch httpx errors in `/login` and `/verify` and return 502 with a generic body.
- **T1.3 [RT] Session protection.** Encrypt `api_session_key` and `ws_session_key` at rest (Fernet, key from Keychain), or keep them in memory only. Purge expired sessions on startup and daily. Serialize `ensure_authenticated` behind an `asyncio.Lock` (F12).
- **T1.4 [RT] API auth.** New `backend/app/core/security.py` with a bearer-token dependency applied to every router in `api/routes/`. Auth and `Origin` check in `ws.py`. Rate limits (`slowapi`) on `/auth/*`, `/account/margin` and `refresh-ltp`. Remove `uid` from `/auth/definedge/session`. Trim `/account/margin` to the fields the UI needs. Tighten CORS methods and headers. Bind uvicorn explicitly to `127.0.0.1` in `scripts/start.sh`. Add the token to `frontend/lib/api.ts`.
- **T1.5 [DEV] Tooling and CI.** `backend/pyproject.toml` (ruff, mypy, pytest, coverage). Pre-commit with gitleaks, detect-secrets and ruff. `.github/workflows/ci.yml` running lint, type-check, tests, secret scan and audits.
- **T1.6 [DEV] Dependencies.** Pin `requirements.txt` with a lock file (`pip-tools` or `uv`), remove the unused `websockets`, and enable Dependabot.

### M2 — Money safety
- **T3.1 [RT] Execution guard.** New `backend/app/core/execution_guard.py` and a `TRADING_MODE` setting (default `disabled`). No order methods on `DefinedgeClient`. Any future order path must pass the guard: dry-run default, kill switch, max order value, max daily loss, symbol allowlist, and an explicit human confirmation token.
- **T3.2 [DEV]** CI test that fails if `placeorder`, `modifyorder`, `cancelorder` or similar appear in `backend/app` outside the guard.
- **T3.3 [RT] Data provenance.** Add `data_source: live|clickhouse|sample` to every signal (`schemas.py`, `screener_loop.py`). Refuse to emit signals from the sample chain unless `ALLOW_SAMPLE_DATA=1`. Reject quotes older than N seconds. Return a degraded status through the API, and show a banner in the UI.
- **T3.4 [RT] Field validation** in `schemas.py`: `qty` an int with 0 < qty ≤ lot cap, `price` and `strike` finite and > 0 (no NaN or Inf), `symbol` checked against a regex and the instrument master, and a max length on notes and journal text.
- **T3.5 [RT] Server-owned P&L.** Remove `status`, `exit_time` and `realized_pnl` from `PositionUpdate` (`schemas.py:131-135`). Compute P&L with `Decimal` (or integer paise) in `positions.py:25-27`.
- **T3.6 [RT] Atomic, idempotent exits.** `exit-all` runs in one transaction (`positions.py:229-230`), and a second exit on the same leg is rejected.
- **T3.7 [RT] DB integrity.** In `db/sqlite.py`: `PRAGMA foreign_keys=ON`, WAL, busy timeout, a CHECK on `strategy_type` in `position_logs`, and an append-only `audit_log` table with a hash chain for every money event. Add a versioned migration table.
- **T3.8 [RT] Error handling.** Replace the silent `except Exception` blocks (F9) with typed, logged handling that surfaces degraded status. Close the leaked `httpx.AsyncClient` in `get_instrument_master`. Validate the downloaded instrument zip (size limit, expected columns).
- **T3.9 [RT] Frontend.** Validate `window.prompt` input (`PositionsPnlPanel.tsx:82,96`) for NaN and negatives, with a confirm dialog showing P&L before exit. Fix the `entry_price: 0` default (`frontend/lib/api.ts:179`).

### M3 — Correctness and evals
- **T4.1 [RT] Fix signal math.** In `engine.py`: proper joint iron condor POP (`:129`) and both breakevens (`:157`). In `pricing.py`: guards for T→0, σ→0 and non-positive spot or strike (`_d1_d2`). In `screener_loop.py`: handle same-day expiry (`:109`) and remove or explicitly label the synthetic IV history (`:138-139`).
- **T4.2 [BOTH] Invariants.** New `backend/app/screener/invariants.py`: max_loss > 0, POP in [0,1], breakevens consistent with the strikes, credit < width, `data_source` present. Runtime: drop and log violating signals. Evals: scored.
- **T5.1 [DEV] Pricing golden set.** `backend/evals/pricing_golden.jsonl`, about 200 cases generated from QuantLib or py_vollib (test-only dependency): price and Greeks. Metrics: max absolute and relative error, with thresholds.
- **T5.2 [DEV] Screener golden chains.** `backend/evals/screener_golden/`: frozen anonymized chain snapshots with expected signals (strategy, strikes, credit, max loss, breakevens, POP). Adversarial cases: missing strikes, zero bid/ask, crossed markets, wide spreads, same-day expiry, stale data.
- **T5.3 [DEV] Runner.** `python -m evals.run` writes a JSON report. CI fails on regression against a stored baseline.

### Testing track (runs across milestones)
Layout: `backend/tests/{unit,api,contract,property,security,concurrency,resilience,e2e}/`. Tools: pytest, pytest-asyncio, httpx `ASGITransport`, respx, hypothesis, pytest-cov. The frontend uses Vitest, Testing Library and Playwright.
- **T2.1 [DEV]** Test scaffolding: fixtures with a temp SQLite per test, a fake broker client, and a frozen clock.
- **T2.2 [DEV]** Security tests: log-capture redaction across login, failure and positions flows (no token, secret, TOTP, session key or full uid), response-secret scan, 401 on every route and WebSocket, bad-Origin WebSocket, CORS.
- **T2.3 [DEV]** Unit tests for `pricing.py`, `technicals.py`, `engine.py`, `_leg_pnl`, payoff and breakevens in `analytics.py`, `instruments.py` parsing, and `config.py`. The iron condor bug gets a failing-first regression test.
- **T2.4 [DEV]** API tests for validation (negative, zero, NaN, huge, injection strings), forbidden PATCH fields, `exit-all` atomicity, double-exit idempotency, rate limits, and error bodies free of secrets.
- **T2.5 [DEV]** Guard tests: no order methods, sample data refused, stale data rejected, degraded status reported.
- **T2.6 [DEV]** Property tests (hypothesis): put-call parity, delta in [-1,1], price monotonic in vol, IV(price(σ)) ≈ σ, spread max loss = width − credit.
- **T2.7 [DEV]** Contract tests for `definedge_client.py` using recorded, anonymized fixtures (respx): HTTP 200 with `status: ERROR` for an expired session, CSV history, malformed instrument master, the TOTP login hash.
- **T2.8 [DEV]** Concurrency tests (two simultaneous `ensure_authenticated` calls produce one login) and resilience tests (broker down, ClickHouse down).
- **T2.9 [DEV]** Frontend tests (`PositionsPnlPanel`, `lib/format.ts`, the sample-data banner) and a Playwright e2e: log a position, exit it and verify P&L.
- **Coverage gates:** 90% on `screener/` and the money-handling code in `positions.py`, 80% overall for the backend.

### M4 — Hardening
- **T6.1** Contract, concurrency, resilience and frontend suites complete (T2.7–T2.9).
- **T6.2** CI gates: coverage, evals, gitleaks, pip-audit, npm audit all required to merge.
- **T6.3** Optional opt-in scheduled read-only live smoke test against the broker.

### M5 — Later
- **T7.1 [RT]** Retention job: purge sessions older than 24h and logs older than 14 days. Journal export and delete command. `docs/data-inventory.md`. Optional encrypted backups of `app.db`.
- **T7.2 [DEV]** Mutation testing (`mutmut`) on `pricing.py` and `engine.py`.
- **T7.3 [BOTH]** Calibration monitoring: once ClickHouse has data, a walk-forward backtest comparing predicted POP with realized outcome (calibration curve, Brier score), with an alert on drift.
- **T7.4 [DEV]** LLM evals, only if "Reason for Trade" or other LLM features are added:
  - Faithfulness: numbers in generated text must match the engine output exactly.
  - Safety: the model has no order tools and its output never reaches the execution path. Prompt-injection cases must not change signals or leak secrets (canary tokens).
  - PII and secrets: model inputs go through the same redaction filter.
  - Grading: deterministic checks first, an LLM judge second (calibrated on about 30 human-labelled examples). Pin the model ID, and rerun on every prompt change.

## 5. Critical files
Modify: `backend/app/main.py`, `config.py`, `integrations/definedge_client.py`, `api/routes/{auth,account,positions,ws,market,chain}.py`, `screener/{engine,pricing}.py`, `workers/screener_loop.py`, `db/sqlite.py`, `models/schemas.py`, `backend/docker-compose.yml`, `backend/requirements.txt`, `scripts/start.sh`, `frontend/lib/api.ts`, `frontend/components/PositionsPnlPanel.tsx`.

New: `backend/app/core/{logging,security,execution_guard}.py`, `backend/app/screener/invariants.py`, `backend/tests/`, `backend/evals/`, `backend/pyproject.toml`, `.github/workflows/ci.yml`, `.pre-commit-config.yaml`, `.gitignore`, `docs/data-inventory.md`.

## 6. Verification
- `pytest backend/tests -q --cov` is green with the coverage gates met. The redaction, no-order-methods and 401 tests pass.
- `python -m evals.run` shows pricing error within thresholds, all signal invariants passing, and no regression against baseline.
- Manual: run `scripts/start.sh`, log in, then grep `logs/backend.log` and `app.db` for the token, secret, TOTP and session key. There must be zero matches. Call every route without a token and expect 401. Start with the broker disabled, and confirm the UI shows a degraded banner and no sample signals.
- `gitleaks detect`, `pip-audit` and `npm audit` are clean in CI.

## 7. Decisions (resolved)
1. Secret store: **`.env`** (mode 600, gitignored). No Keychain.
2. API auth: **static bearer token** (`API_AUTH_TOKEN`).
3. LLM feature: **yes — "Reason for Trade"** (implemented, off by default; `LLM_REASON_ENABLED`).

## 8. Implementation status (2026-10-01)
| Task | Status | Notes |
|---|---|---|
| T0.1 rotate broker credentials | **MANUAL — still required** | Cannot be done from code. The old token was in `logs/backend.log` (now redacted) and the TOTP secret sat in an unprotected folder. |
| T0.2–T0.5 | Done | Root `.gitignore`, `git init`, `.env` chmod 600, logs scrubbed, old sessions purged, ClickHouse bound to 127.0.0.1 with password from env. |
| T1.1–T1.4 | Done | `core/logging.py` (redaction), safe errors, Fernet session encryption + login lock + purge, bearer auth on all routes and WebSockets, rate limits, tight CORS, loopback bind. |
| T1.5–T1.6 | Done | `pyproject.toml`, pinned `requirements*.txt`, `.github/workflows/ci.yml`, `.pre-commit-config.yaml`, Dependabot. (CI/pre-commit have not run: no remote yet; gitleaks/pip-audit not run locally.) |
| T3.1–T3.2 | Done | `core/execution_guard.py` + static "no order paths" test. |
| T3.3–T3.9 | Done | `data_source` labels, sample data refused unless `ALLOW_SAMPLE_DATA`, stale-signal withholding, field validation, server-owned P&L (Decimal), atomic idempotent exits, hash-chained audit log, DB triggers, typed error handling, frontend validation + banner. |
| T4.1–T4.2 | Done | Iron condor POP/breakevens/max-loss fixed, pricing guards, time-to-expiry to 15:30 IST, no fabricated IV history, shared `invariants.py`. |
| T5.1–T5.3 | Done | `backend/evals/` (200-case pricing set from an independent implementation, 4 hand-computed chains, reason-validator cases, baseline gate). Mutation-checked: reintroducing the old bugs fails the evals. |
| T2.x tests | Done | 149 backend tests (unit, property, API, contract, security, concurrency, resilience) + 18 frontend tests. Coverage 84% overall, 92% on money/screener/security code. |
| T7.1 | Partly | Daily session purge; log rotation. `docs/data-inventory.md` written. Journal export/delete and encrypted DB backups: **deferred by owner** (journal table unused; backup destination/key undecided). |
| T7.2, T7.3 | **Deferred by owner** | Mutation testing; calibration backtest (needs months of ClickHouse option history, which doesn't exist yet). |
| T7.4 LLM | Done | `app/reasoning/`: closed numeric facts only, faithfulness/safety validator, template fallback, per-run cap, cache. Live model calls are untested (mocked only). |
| T2.9 Playwright e2e | **Deferred by owner** | Build once the UI settles. |
