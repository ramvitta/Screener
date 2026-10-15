import json
from typing import List

from fastapi import APIRouter, HTTPException

from app.db.sqlite import get_conn
from app.models.schemas import (
    ScreenerRule,
    ScreenerRun,
    ScreenerRunDetail,
    SignalHistoryDetail,
    SignalHistoryItem,
)
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


def _run_model(r) -> ScreenerRun:
    return ScreenerRun(
        run_id=r["run_id"],
        started_at=r["started_at"],
        data_source=r["data_source"],
        status=r["status"],
        message=r["message"] or "",
        signal_count=r["signal_count"],
        created_at=r["created_at"],
    )


def _signal_model(r, detail: bool = False):
    base = {
        "id": r["id"],
        "run_id": r["run_id"],
        "signal_id": r["signal_id"],
        "strategy_type": r["strategy_type"],
        "symbol": r["symbol"],
        "expiry": r["expiry"],
        "status": r["status"],
        "reject_reason": r["reject_reason"],
        "generated_at": r["generated_at"],
        "data_source": r["data_source"],
        "reason": r["reason"],
        "reason_source": r["reason_source"],
        "created_at": r["created_at"],
    }
    if detail:
        base["signal_json"] = json.loads(r["signal_json"]) if r["signal_json"] else None
        base["decision_json"] = json.loads(r["decision_json"]) if r["decision_json"] else None
        return SignalHistoryDetail(**base)
    return SignalHistoryItem(**base)


@router.get("/runs", response_model=List[ScreenerRun])
def list_runs(limit: int = 50, offset: int = 0):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM screener_runs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return [_run_model(r) for r in rows]


@router.get("/runs/{run_id}", response_model=ScreenerRunDetail)
def get_run(run_id: str):
    with get_conn() as conn:
        run = conn.execute("SELECT * FROM screener_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        sigs = conn.execute("SELECT * FROM screener_signals WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    detail = _run_model(run).model_dump()
    detail["signals"] = [_signal_model(s, detail=True) for s in sigs]
    return ScreenerRunDetail(**detail)


@router.get("/history", response_model=List[SignalHistoryItem])
def list_history(limit: int = 100, offset: int = 0, status: str = None, strategy_type: str = None, symbol: str = None):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    query = "SELECT * FROM screener_signals"
    clauses = []
    args = []
    if status:
        clauses.append("status = ?")
        args.append(status)
    if strategy_type:
        clauses.append("strategy_type = ?")
        args.append(strategy_type)
    if symbol:
        clauses.append("symbol = ?")
        args.append(symbol.upper())
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with get_conn() as conn:
        rows = conn.execute(query, args).fetchall()
    return [_signal_model(r) for r in rows]


@router.get("/history/{history_id}", response_model=SignalHistoryDetail)
def get_history(history_id: int):
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM screener_signals WHERE id = ?", (history_id,)).fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Signal not found")
    return _signal_model(r, detail=True)
