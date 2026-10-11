import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.integrations.definedge_client import DefinedgeAuthError
from app.workers.screener_loop import run_screener_job

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# Well inside the session's 12h lifetime (and ensure_authenticated()'s own
# 30-min re-auth buffer), so a long-running process never lets the Definedge
# session go stale mid-market-hours.
DEFINEDGE_REAUTH_INTERVAL_MINUTES = 60


async def _reauth_job() -> None:
    from app.api.routes.auth import ensure_authenticated

    try:
        await ensure_authenticated()
    except DefinedgeAuthError as exc:
        logger.warning("Scheduled Definedge re-auth failed (%s) — will retry next tick.", exc)


async def _retention_job() -> None:
    """Daily housekeeping: delete expired broker sessions past retention."""
    from app.api.routes.auth import purge_expired_sessions

    try:
        removed = purge_expired_sessions()
        if removed:
            logger.info("Retention: purged %d expired broker session rows", removed)
    except Exception as exc:  # noqa: BLE001 — housekeeping must never crash the scheduler
        logger.warning("Retention job failed (%s)", type(exc).__name__)


def start_scheduler() -> None:
    scheduler.add_job(
        run_screener_job,
        "interval",
        minutes=settings.screener_interval_minutes,
        id="screener_run",
        next_run_time=None,  # first run triggered explicitly at startup, see main.py
    )
    scheduler.add_job(
        _reauth_job,
        "interval",
        minutes=DEFINEDGE_REAUTH_INTERVAL_MINUTES,
        id="definedge_reauth",
    )
    scheduler.add_job(_retention_job, "interval", hours=24, id="retention")
    scheduler.start()
    logger.info(
        "Scheduler started: screener runs every %s min, Definedge re-auth every %s min",
        settings.screener_interval_minutes,
        DEFINEDGE_REAUTH_INTERVAL_MINUTES,
    )


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
