import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.integrations.definedge_client import DefinedgeAuthError
from app.workers.breadth_loop import run_breadth_job
from app.workers.daily_byte_loop import run_daily_byte_job
from app.workers.screener_loop import run_screener_job
from app.workers.str_stra_loop import run_str_stra_job
from app.workers.telegram_calls_loop import run_sync_job, run_tracking_job

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
    scheduler.add_job(
        run_str_stra_job,
        "interval",
        minutes=settings.str_stra_interval_minutes,
        id="str_stra_run",
        next_run_time=None,  # first run triggered explicitly at startup, see main.py
    )
    scheduler.add_job(
        run_breadth_job,
        "interval",
        minutes=settings.breadth_interval_minutes,
        id="breadth_run",
        next_run_time=None,  # first run triggered explicitly at startup, see main.py
    )
    scheduler.add_job(
        run_sync_job,
        "interval",
        minutes=settings.telegram_call_sync_interval_minutes,
        id="telegram_call_sync",
        max_instances=1,
        coalesce=True,
        # Do one incremental pass shortly after boot, then every N minutes.
        # Without this a process that is restarted before the first interval
        # never syncs at all, leaving the tracker stuck on its old data.
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
    )
    scheduler.add_job(
        run_tracking_job,
        "interval",
        minutes=settings.telegram_call_track_interval_minutes,
        id="telegram_call_track",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=90),
    )
    scheduler.add_job(
        run_daily_byte_job,
        CronTrigger(
            hour=settings.telegram_daily_byte_hour,
            minute=settings.telegram_daily_byte_minute,
            timezone=settings.telegram_daily_byte_timezone,
        ),
        id="daily_byte_run",
    )
    scheduler.start()
    logger.info(
        "Scheduler started: screener every %s min, str-stra every %s min, breadth every %s min, "
        "daily-byte at %02d:%02d %s, trade-call sync every %s min, trade-call track every %s min, "
        "Definedge re-auth every %s min",
        settings.screener_interval_minutes,
        settings.str_stra_interval_minutes,
        settings.breadth_interval_minutes,
        settings.telegram_daily_byte_hour,
        settings.telegram_daily_byte_minute,
        settings.telegram_daily_byte_timezone,
        settings.telegram_call_sync_interval_minutes,
        settings.telegram_call_track_interval_minutes,
        DEFINEDGE_REAUTH_INTERVAL_MINUTES,
    )


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
