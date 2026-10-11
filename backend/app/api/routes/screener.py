import json
from typing import List

from fastapi import APIRouter, HTTPException

from app.db.sqlite import get_conn
from app.models.schemas import ScreenerRule
from app.workers.screener_loop import get_latest_signals, get_status

router = APIRouter(prefix="/api/v1/screener", tags=["screener"])


@router.get("/signals")
def latest_signals():
    return get_latest_signals()


@router.get("/status")
def screener_status():
    """Data provenance + health for the UI banner: ok (live data), sample
    (dev fixture), or degraded (no real data — no signals are produced)."""
    return get_status()


@router.get("/rules", response_model=List[ScreenerRule])
def list_rules():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM screener_rules ORDER BY id").fetchall()
    return [
        ScreenerRule(
            id=r["id"],
            strategy_type=r["strategy_type"],
            symbol=r["symbol"],
            is_active=bool(r["is_active"]),
            params=json.loads(r["params_json"]),
        )
        for r in rows
    ]


@router.post("/rules", response_model=ScreenerRule, status_code=201)
def upsert_rule(rule: ScreenerRule):
    with get_conn() as conn:
        if rule.id:
            existing = conn.execute("SELECT id FROM screener_rules WHERE id = ?", (rule.id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Rule not found")
            conn.execute(
                "UPDATE screener_rules SET strategy_type=?, symbol=?, is_active=?, params_json=? WHERE id=?",
                (rule.strategy_type.value, rule.symbol, int(rule.is_active), json.dumps(rule.params), rule.id),
            )
            row_id = rule.id
        else:
            cur = conn.execute(
                "INSERT INTO screener_rules (strategy_type, symbol, is_active, params_json) VALUES (?, ?, ?, ?)",
                (rule.strategy_type.value, rule.symbol, int(rule.is_active), json.dumps(rule.params)),
            )
            row_id = cur.lastrowid
        row = conn.execute("SELECT * FROM screener_rules WHERE id = ?", (row_id,)).fetchone()

    return ScreenerRule(
        id=row["id"],
        strategy_type=row["strategy_type"],
        symbol=row["symbol"],
        is_active=bool(row["is_active"]),
        params=json.loads(row["params_json"]),
    )
