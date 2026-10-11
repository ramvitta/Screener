import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import account, admin, alerts, analytics, auth, backfill, chain, charts, market, positions, screener, ws
from app.api.routes.auth import ensure_authenticated, purge_expired_sessions
from app.config import settings
from app.core.logging import new_request_id, register_settings_secrets, request_id_var, setup_logging
from app.core.security import require_auth
from app.db import sqlite
from app.integrations.definedge_client import DefinedgeAuthError
from app.workers.scheduler import start_scheduler, stop_scheduler
from app.workers.screener_loop import run_screener_job

LOG_FILE = Path(__file__).resolve().parents[2] / "logs" / "backend.log"

register_settings_secrets(settings)
setup_logging(None if os.environ.get("APP_ENV") == "test" else LOG_FILE)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.api_auth_token or len(settings.api_auth_token) < 24:
        # Fail closed: never run an unauthenticated money app.
        raise RuntimeError("API_AUTH_TOKEN must be set (>= 24 chars) in backend/.env — refusing to start.")
    if settings.trading_mode != "disabled":
        raise RuntimeError("TRADING_MODE must be 'disabled': order placement is not implemented.")

    sqlite.init_db()
    purged = purge_expired_sessions()
    if purged:
        logger.info("Purged %d expired broker session rows", purged)
    try:
        from app.db import clickhouse

        clickhouse.init_db()
    except Exception as exc:
        logger.warning("ClickHouse not available at startup (%s) — run `docker compose up` for it.", type(exc).__name__)

    try:
        # Auto-login via TOTP (or restore a still-valid session from SQLite)
        # so the platform is authenticated with Definedge without a human
        # completing a manual OTP step — see auth.py::ensure_authenticated.
        await ensure_authenticated()
    except DefinedgeAuthError as exc:
        logger.warning(
            "Definedge auto-login failed at startup (%s) — falling back to manual "
            "/api/v1/auth/definedge/login + /verify, or check DEFINEDGE_TOTP_SECRET.",
            exc,
        )

    start_scheduler()
    await run_screener_job()  # populate the cache immediately instead of waiting for the first interval
    yield
    stop_scheduler()


app = FastAPI(title="Options Screener & Monitor API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,  # bearer token in a header; no cookies are used
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """FastAPI's default 422 body echoes the submitted `input` — which both
    crashes on NaN/Inf (not JSON-serialisable → 500) and would reflect
    whatever the client sent. Return only where/what was wrong."""
    errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Request-ID for log correlation + defensive response headers."""
    rid = request.headers.get("x-request-id", "")[:32] or new_request_id()
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


_protected = [Depends(require_auth)]
for _router in (
    auth.router,
    account.router,
    market.router,
    charts.router,
    analytics.router,
    chain.router,
    screener.router,
    positions.router,
    alerts.router,
    backfill.router,
    admin.router,
):
    app.include_router(_router, dependencies=_protected)
app.include_router(ws.router)  # authenticates itself per-connection (core/security.py)


@app.get("/api/v1/health")
def health():
    """Unauthenticated liveness probe — reveals nothing beyond up/down."""
    return {"status": "ok"}
