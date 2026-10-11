"""Admin historical-data-download page (proactive archival of currently-
listed F&O stock contracts — see integrations/history_downloader.py for why
expired contracts are out of scope). Registered under the same bearer-auth
dependency as every other router (main.py's `_protected` list) — the
original plan's "no auth on admin" note predates the security hardening
pass and no longer applies."""

from typing import List

from fastapi import APIRouter, HTTPException

from app.api.routes.auth import _client, ensure_authenticated
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
    assert job is not None
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
    assert updated is not None
    return updated


@router.get("/coverage", response_model=List[DownloadCoverageRow])
def coverage(symbol: Symbol):
    return history_downloader.coverage(symbol)
