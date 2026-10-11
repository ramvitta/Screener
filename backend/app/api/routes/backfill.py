from typing import List

from fastapi import APIRouter

from app.db.sqlite import get_conn
from app.models.schemas import BackfillStatus

router = APIRouter(prefix="/api/v1/backfill", tags=["backfill"])


@router.get("/status", response_model=List[BackfillStatus])
def backfill_status():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM backfill_progress ORDER BY expiry, strike").fetchall()
    return [
        BackfillStatus(
            contract_id=r["contract_id"],
            symbol=r["symbol"],
            expiry=r["expiry"],
            strike=r["strike"],
            option_type=r["option_type"],
            last_completed_date=r["last_completed_date"],
            status=r["status"],
        )
        for r in rows
    ]
