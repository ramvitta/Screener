# Data inventory & handling

| Data | Where it lives | Sensitivity | Protection |
|---|---|---|---|
| Definedge API token / secret / TOTP secret | `backend/.env` (mode 600, gitignored) | **Critical** — TOTP secret can mint broker logins | Never logged (registered with the redaction filter + pattern scrubbing); never returned by any route; rotate if the folder is ever shared |
| `API_AUTH_TOKEN` (bearer) | `backend/.env`, `frontend/.env.local` (`NEXT_PUBLIC_`) | High | Constant-time compare; app refuses to start without it; the `NEXT_PUBLIC_` copy is visible to anyone who can open the UI — local use only |
| Broker session keys | `backend/data/app.db` → `broker_sessions` | High (trade-capable session) | Fernet-encrypted at rest with `SESSION_ENCRYPTION_KEY`; not persisted at all if the key is unset; expired rows purged daily |
| Broker user id / account id (`uid`, `actid`) | `broker_sessions` | PII | Masked to last 2 chars in logs; never returned by `/auth/definedge/session`, `/login`, `/relogin`, `/verify` |
| Margin / limits | In-memory passthrough | Financial | `/account/margin` returns an allowlisted subset; the raw payload is never forwarded |
| Positions, legs, P&L | `app.db` → `position_logs`, `position_legs` | Financial | Server-owned P&L (Decimal, paise); every money event hash-chained in `audit_log` (append-only triggers) |
| Notes / journal free text | `position_logs.notes` | May contain PII | Length-capped (2000); never sent to the LLM; never logged |
| Logs | `logs/backend.log` (rotating, 5 MB x 3) | Medium | JSON, request ids, redacted at the handler level |
| LLM prompts (optional feature) | Anthropic API | Medium | Contains only engine-computed market numbers (`reasoning/generator.py::build_facts`) |

Retention: broker sessions purged after `SESSIONS_RETENTION_HOURS` (24h) once expired/inactive; logs rotate (3 x 5 MB). `audit_log` is intentionally never purged.
