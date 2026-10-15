"""Admin historical-data-download page (proactive archival of currently-
listed F&O stock contracts — see integrations/history_downloader.py for why
expired contracts are out of scope). Registered under the same bearer-auth
dependency as every other router (main.py's `_protected` list) — the
original plan's "no auth on admin" note predates the security hardening
pass and no longer applies."""

from datetime import date, datetime, time, timedelta, timezone
from typing import List

from fastapi import APIRouter, HTTPException

from app.api.routes.auth import _client, ensure_authenticated
from app.config import settings
from app.db.sqlite import get_conn
from app.integrations import history_downloader, instruments
from app.integrations.definedge_client import DefinedgeAuthError
from app.models.schemas import (
    DownloadCoverageRow,
    DownloadJobCreate,
    DownloadJobItemStatus,
    DownloadJobStatus,
    DownloadValidateRequest,
    DownloadValidateResponse,
    FnoStock,
    PruneSignalsRequest,
    PruneSignalsResponse,
    Symbol,
)

router = APIRouter(prefix="/api/v1/admin/history", tags=["admin"])


@router.get("/fno-stocks", response_model=List[FnoStock])
async def fno_stocks():
    try:
        await ensure_authenticated()
        rows = await instruments.get_master_rows(_client)
    except DefinedgeAuthError:
        raise HTTPException(status_code=503, detail="Broker session unavailable") from None
    return instruments.list_fno_stocks(rows)


@router.post("/validate", response_model=DownloadValidateResponse)
async def validate(payload: DownloadValidateRequest):
    return await history_downloader.validate(payload.symbol, payload.start_date, payload.end_date, payload.scope)


@router.post("/jobs", response_model=DownloadJobStatus, status_code=201)
async def create_job(payload: DownloadJobCreate):
    check = await history_downloader.validate(payload.symbol, payload.start_date, payload.end_date, payload.scope)
    if not check.items:
        raise HTTPException(status_code=400, detail="Nothing resolvable for this symbol/scope — run /validate first")
    job_id = history_downloader.create_job_row(payload.symbol, payload.start_date, payload.end_date, payload.scope)
    history_downloader.start_job(job_id, payload.symbol, payload.start_date, payload.end_date, payload.scope)
    job = history_downloader.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=500, detail="Job was not persisted after creation")
    return job


@router.get("/jobs", response_model=List[DownloadJobStatus])
def list_jobs():
    return history_downloader.list_jobs()


@router.get("/jobs/{job_id}", response_model=DownloadJobStatus)
def get_job(job_id: int):
    job = history_downloader.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/items", response_model=List[DownloadJobItemStatus])
def get_job_items(job_id: int):
    if not history_downloader.get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return history_downloader.list_job_items(job_id)


@router.post("/jobs/{job_id}/stop", response_model=DownloadJobStatus)
def cancel_job(job_id: int):
    job = history_downloader.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    history_downloader.cancel_job(job_id)
    updated = history_downloader.get_job(job_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Job was not persisted after cancel")
    return updated


@router.get("/coverage", response_model=List[DownloadCoverageRow])
def coverage(symbol: Symbol):
    return history_downloader.coverage(symbol)


@router.post("/prune-signals", response_model=PruneSignalsResponse)
def prune_signals(payload: PruneSignalsRequest):
    """Admin-gated retention prune: deletes signal history older than the
    retention window (or an explicit `before` date) only when the caller
    confirms. There is deliberately no automatic pruning."""
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to prune signal history")
    cutoff_date = payload.before or (date.today() - timedelta(days=settings.signal_history_retention_days))
    cutoff = datetime.combine(cutoff_date, time.min, tzinfo=timezone.utc).isoformat()
    with get_conn() as conn:
        old_run_ids = [r["run_id"] for r in conn.execute("SELECT run_id FROM screener_runs WHERE started_at < ?", (cutoff,)).fetchall()]
        deleted_signals = 0
        for rid in old_run_ids:
            deleted_signals += conn.execute("DELETE FROM screener_signals WHERE run_id = ?", (rid,)).rowcount
        deleted_runs = conn.execute("DELETE FROM screener_runs WHERE started_at < ?", (cutoff,)).rowcount
    return PruneSignalsResponse(deleted_signals=deleted_signals, deleted_runs=deleted_runs, cutoff=cutoff_date)
