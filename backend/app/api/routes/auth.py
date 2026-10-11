import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.core import crypto
from app.core.logging import mask_id, register_secret
from app.core.security import rate_limit
from app.db.sqlite import get_conn
from app.integrations.definedge_client import DefinedgeAuthError, DefinedgeClient, DefinedgeSession

logger = logging.getLogger(__name__)
# Login endpoints can mint broker sessions — keep them tightly rate limited.
router = APIRouter(
    prefix="/api/v1/auth/definedge",
    tags=["auth"],
    dependencies=[Depends(rate_limit(10, 60))],
)
_client = DefinedgeClient()

# Serializes logins: Definedge revokes older tokens when a newer login lands,
# so two concurrent ensure_authenticated() calls must not both log in.
# One lock per running event loop (an asyncio.Lock is bound to the loop it is
# first used on, so a module-level instance breaks if the loop ever differs).
_login_locks: "dict[asyncio.AbstractEventLoop, asyncio.Lock]" = {}


def _login_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _login_locks.get(loop)
    if lock is None:
        _login_locks.clear()  # drop locks of dead loops
        lock = _login_locks[loop] = asyncio.Lock()
    return lock

# Re-authenticate this far ahead of the stored session's expiry, so a
# scheduled refresh (see workers/scheduler.py) always lands on a session with
# headroom left rather than racing an outright expiry mid-request.
_REAUTH_BUFFER = timedelta(minutes=30)


class OtpRequest(BaseModel):
    otp_token: str = Field(min_length=1, max_length=256)
    otp_code: str = Field(pattern=r"^\d{4,8}$")


def _store_session(session: DefinedgeSession) -> None:
    # Make sure the live session keys can never appear in a log line.
    register_secret(session.api_session_key)
    register_secret(session.ws_session_key)
    persist = crypto.encryption_enabled()
    api_key = crypto.encrypt(session.api_session_key) if persist else ""
    ws_key = crypto.encrypt(session.ws_session_key) if persist else ""
    with get_conn() as conn:
        conn.execute("UPDATE broker_sessions SET is_active = 0 WHERE broker = 'definedge'")
        conn.execute(
            """
            INSERT INTO broker_sessions (broker, uid, actid, api_session_key, ws_session_key, issued_at, expires_at, is_active)
            VALUES ('definedge', ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                session.uid,
                session.actid,
                api_key,
                ws_key,
                datetime.now().isoformat(),
                session.expires_at.isoformat(),
            ),
        )


def _deactivate_sessions() -> None:
    """Wired up as _client.on_invalidated below — runs when a call discovers
    the live session was revoked server-side (e.g. a newer login elsewhere),
    so ensure_authenticated()'s DB-restore step won't keep resurrecting the
    same dead session on every subsequent call."""
    with get_conn() as conn:
        conn.execute("UPDATE broker_sessions SET is_active = 0 WHERE broker = 'definedge'")


_client.on_invalidated = _deactivate_sessions


def _load_session_from_db() -> Optional[DefinedgeSession]:
    """Restores the most recent active session row, if it hasn't expired —
    lets a process restart pick up where it left off instead of forcing a
    fresh TOTP login every time the backend restarts."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM broker_sessions WHERE broker = 'definedge' AND is_active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    expires_at = datetime.fromisoformat(row["expires_at"])
    if datetime.now() >= expires_at:
        return None
    # Rows without an encryption key (or encrypted under a rotated key) are
    # not restorable — fall through to a fresh TOTP login.
    api_key = crypto.decrypt(row["api_session_key"])
    ws_key = crypto.decrypt(row["ws_session_key"])
    if not api_key or not ws_key:
        return None
    register_secret(api_key)
    register_secret(ws_key)
    return DefinedgeSession(
        uid=row["uid"],
        actid=row["actid"],
        api_session_key=api_key,
        ws_session_key=ws_key,
        expires_at=expires_at,
    )


async def ensure_authenticated(force: bool = False) -> DefinedgeSession:
    async with _login_lock():
        return await _ensure_authenticated_locked(force)


async def _ensure_authenticated_locked(force: bool) -> DefinedgeSession:
    """Single entry point every authenticated call/worker should go through
    instead of assuming a prior manual login happened. In order:
    1. Already-live in-memory session with headroom left → return it as-is.
    2. A still-valid session persisted in SQLite → restore it (covers a
       process restart without a fresh login).
    3. `DEFINEDGE_TOTP_SECRET` configured → auto-login via TOTP (no manual
       OTP step) and persist the result.
    4. Otherwise → DefinedgeAuthError; the manual /login + /verify pair
       remains the fallback for accounts with no TOTP secret configured.

    `force=True` skips step 1's short-circuit (used by POST /relogin to get
    a guaranteed-fresh session on demand).

    Local `expires_at` tracking only proves the session hasn't timed out on
    our clock — Definedge can revoke it server-side at any moment (e.g. a
    newer login elsewhere invalidates the older token), and signals that two
    different ways: a real 401, or an HTTP 200 with a JSON
    `{"status": "ERROR"}` envelope (see DefinedgeClient._authed_get). A
    restored-from-DB session is therefore verified with one cheap live call
    before being trusted, rather than assumed good — this was the root
    cause of charts silently failing against a session that looked "active"
    locally but had already been revoked broker-side."""
    if not force and _client._session and not _client._session.expires_within(_REAUTH_BUFFER):
        return _client._session

    if not force:
        restored = _load_session_from_db()
        if restored and not restored.expires_within(_REAUTH_BUFFER):
            _client._session = restored
            try:
                await _client.get_limits()  # cheap authenticated call, purely to verify the session still works
                logger.info("Definedge session restored from SQLite (uid=%s, expires_at=%s)", mask_id(restored.uid), restored.expires_at)
                return restored
            except DefinedgeAuthError:
                logger.warning("Restored Definedge session was rejected by the server — falling through to a fresh login")
                # _authed_get already cleared _client._session and deactivated
                # the DB row; fall through to the TOTP-login branch below.

    if not settings.definedge_totp_secret:
        raise DefinedgeAuthError(
            "No valid session and DEFINEDGE_TOTP_SECRET is not set — cannot auto-login. "
            "Use POST /api/v1/auth/definedge/login + /verify with a manually supplied OTP instead."
        )

    try:
        session = await _client.login()
    except DefinedgeAuthError:
        raise
    except Exception as exc:
        # Definedge's TOTP token exchange can fail transiently (network
        # hiccup, or a 400 if a login lands too close to a prior one within
        # the same TOTP window) — every caller of ensure_authenticated()
        # (startup, the hourly reauth job, on-demand routes) only handles
        # DefinedgeAuthError, so any failure here must funnel into that one
        # type rather than crashing the caller (a raw httpx error out of
        # main.py's startup lifespan previously took the whole app down).
        # Only the exception *type* — httpx messages embed the request URL,
        # and the login URL carries the API token in its path.
        raise DefinedgeAuthError(f"Definedge auto-login failed ({type(exc).__name__})") from None
    _store_session(session)
    logger.info("Definedge auto-login via TOTP succeeded (uid=%s, expires_at=%s)", mask_id(session.uid), session.expires_at)
    return session


def _broker_unavailable(exc: Exception) -> HTTPException:
    logger.warning("Definedge login step failed (%s)", type(exc).__name__)
    return HTTPException(status_code=502, detail="Broker login failed")


@router.post("/login")
async def start_login():
    """Manual-OTP fallback, step 1 — only needed when DEFINEDGE_TOTP_SECRET
    isn't configured; the normal path is automatic via ensure_authenticated()."""
    try:
        otp_token = await _client.login_step1()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise _broker_unavailable(exc) from None
    register_secret(otp_token)
    return {"otp_token": otp_token, "message": "OTP sent to registered device/email"}


async def _forced_login() -> dict:
    try:
        session = await ensure_authenticated(force=True)
    except DefinedgeAuthError:
        raise HTTPException(status_code=502, detail="Broker login failed") from None
    return {"expires_at": session.expires_at.isoformat()}


@router.post("/login/auto")
async def auto_login():
    """Triggers the same TOTP auto-login ensure_authenticated() already runs
    automatically at startup and on a schedule — kept for manual testing."""
    return await _forced_login()


@router.post("/relogin")
async def relogin():
    """Operational escape hatch: forces a brand-new session even if one is
    already valid, without restarting the process."""
    return await _forced_login()


@router.post("/verify")
async def verify_login(payload: OtpRequest):
    """Manual-OTP fallback, step 2 — see start_login()."""
    try:
        async with _login_lock():
            session = await _client.login_step2(payload.otp_token, payload.otp_code)
            _store_session(session)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise _broker_unavailable(exc) from None
    return {"expires_at": session.expires_at.isoformat()}


@router.get("/session")
def session_status():
    """Only whether a session is live and when it expires — the broker user
    id and account id are never returned."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_active, expires_at FROM broker_sessions WHERE broker = 'definedge' AND is_active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return {"is_active": False}
    return {"is_active": bool(row["is_active"]), "expires_at": row["expires_at"]}


def purge_expired_sessions() -> int:
    """Deletes inactive/expired session rows past the retention window, so
    stale session material doesn't accumulate in app.db."""
    cutoff = (datetime.now() - timedelta(hours=settings.sessions_retention_hours)).isoformat()
    with get_conn() as conn:
        # Rows written before at-rest encryption existed hold plaintext keys:
        # remove them immediately, regardless of age.
        legacy = conn.execute(
            "DELETE FROM broker_sessions WHERE api_session_key != '' AND api_session_key NOT LIKE 'enc:%'"
        ).rowcount
        cur = conn.execute(
            "DELETE FROM broker_sessions WHERE (is_active = 0 OR expires_at < ?) AND issued_at < ?",
            (datetime.now().isoformat(), cutoff),
        )
        return cur.rowcount + legacy
