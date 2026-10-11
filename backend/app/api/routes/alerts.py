import json
from typing import List

from fastapi import APIRouter

from app.db.sqlite import get_conn
from app.models.schemas import Alert

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


@router.get("", response_model=List[Alert])
def list_alerts(unread_only: bool = False):
    with get_conn() as conn:
        query = "SELECT * FROM alerts"
        if unread_only:
            query += " WHERE is_read = 0"
        query += " ORDER BY created_at DESC LIMIT 100"
        rows = conn.execute(query).fetchall()

    return [
        Alert(
            id=r["id"],
            alert_type=r["alert_type"],
            strategy_type=r["strategy_type"],
            symbol=r["symbol"],
            payload=json.loads(r["payload_json"]),
            is_read=bool(r["is_read"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.patch("/{alert_id}/read", status_code=204)
def mark_read(alert_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE alerts SET is_read = 1 WHERE id = ?", (alert_id,))
