import json
import logging
import math
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.auth import _client, ensure_authenticated
from app.core.money import leg_pnl, sum_money
from app.core.security import rate_limit
from app.db import audit
from app.db.sqlite import get_conn
from app.integrations import instruments
from app.integrations.definedge_client import DefinedgeAuthError
from app.models.schemas import (
    LegExitRequest,
    Position,
    PositionCreate,
    PositionLeg,
    PositionLegUpdate,
    PositionStatus,
    PositionUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/positions", tags=["positions"])

# Column names interpolated into UPDATE statements come only from this
# allowlist, never from request input.
_LEG_THRESHOLD_COLUMNS = {"sl", "tsl_trigger", "tsl_step", "target", "roll_to_strike"}


_leg_pnl = leg_pnl  # kept as a module-level name; Decimal-based, rounded to paise


def _row_to_leg(row) -> PositionLeg:
    is_open = row["closed_at"] is None
    price = row["ltp"] if is_open else row["exit_price"]
    pnl = _leg_pnl(row["action"], row["entry_price"], price, row["qty"]) if price is not None else 0.0
    return PositionLeg(
        id=row["id"],
        strike=row["strike"],
        option_type=row["option_type"],
        action=row["action"],
        qty=row["qty"],
        entry_price=row["entry_price"],
        ltp=row["ltp"],
        sl=row["sl"],
        tsl_trigger=row["tsl_trigger"],
        tsl_step=row["tsl_step"],
        target=row["target"],
        roll_to_strike=row["roll_to_strike"],
        closed_at=row["closed_at"],
        exit_price=row["exit_price"],
        realized_pnl=row["realized_pnl"] if not is_open else None,
        unrealized_pnl=pnl if is_open else 0.0,
    )


def _row_to_position(conn, row) -> Position:
    leg_rows = conn.execute(
        "SELECT * FROM position_legs WHERE position_id = ? ORDER BY id", (row["id"],)
    ).fetchall()
    legs = [_row_to_leg(r) for r in leg_rows]
    unrealized = sum_money(leg.unrealized_pnl for leg in legs)
    booked = sum_money(leg.realized_pnl or 0.0 for leg in legs if leg.closed_at is not None)
    return Position(
        id=row["id"],
        strategy_type=row["strategy_type"],
        symbol=row["symbol"],
        expiry=row["expiry"],
        legs=legs,
        entry_time=row["entry_time"],
        exit_time=row["exit_time"],
        status=row["status"],
        profit_target=row["profit_target"],
        max_loss=row["max_loss"],
        realized_pnl=row["realized_pnl"],
        unrealized_pnl=unrealized,
        booked_pnl=booked,
        notes=row["notes"],
    )


@router.get("", response_model=List[Position])
def list_positions(status: Optional[PositionStatus] = None):
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM position_logs WHERE status = ? ORDER BY entry_time DESC", (status.value,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM position_logs ORDER BY entry_time DESC").fetchall()
        return [_row_to_position(conn, r) for r in rows]


@router.get("/{position_id}", response_model=Position)
def get_position(position_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM position_logs WHERE id = ?", (position_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Position not found")
        return _row_to_position(conn, row)


@router.post("", response_model=Position, status_code=201)
def create_position(payload: PositionCreate):
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO position_logs
                (strategy_type, symbol, expiry, legs_json, entry_time, profit_target, max_loss, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.strategy_type.value,
                payload.symbol,
                payload.expiry.isoformat(),
                json.dumps([leg.model_dump(mode="json") for leg in payload.legs]),
                payload.entry_time.isoformat(),
                payload.profit_target,
                payload.max_loss,
                payload.notes,
            ),
        )
        position_id = cur.lastrowid
        for leg in payload.legs:
            conn.execute(
                """
                INSERT INTO position_legs
                    (position_id, strike, option_type, action, qty, entry_price, ltp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position_id,
                    leg.strike,
                    leg.option_type.value,
                    leg.action.value,
                    leg.qty,
                    leg.entry_price,
                    leg.entry_price,  # ltp seeds at entry_price until a refresh/live tick updates it
                ),
            )
        audit.record(
            conn,
            "position_created",
            {"position_id": position_id, "symbol": payload.symbol, "strategy": payload.strategy_type.value,
             "legs": [leg.model_dump(mode="json") for leg in payload.legs]},
        )
        row = conn.execute("SELECT * FROM position_logs WHERE id = ?", (position_id,)).fetchone()
        return _row_to_position(conn, row)


@router.patch("/{position_id}", response_model=Position)
def update_position(position_id: int, payload: PositionUpdate):
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM position_logs WHERE id = ?", (position_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Position not found")

        if "notes" in payload.model_fields_set:
            conn.execute("UPDATE position_logs SET notes = ? WHERE id = ?", (payload.notes, position_id))
        row = conn.execute("SELECT * FROM position_logs WHERE id = ?", (position_id,)).fetchone()
        return _row_to_position(conn, row)


@router.patch("/{position_id}/legs/{leg_id}", response_model=Position)
def update_leg(position_id: int, leg_id: int, payload: PositionLegUpdate):
    """Sets SL / trailing-SL / target / roll-to-strike as alert thresholds and
    roll intentions the position monitor watches — this never places a
    broker order (read-only execution model, see SRS_Architecture.md §1.1)."""
    with get_conn() as conn:
        leg = conn.execute(
            "SELECT * FROM position_legs WHERE id = ? AND position_id = ?", (leg_id, position_id)
        ).fetchone()
        if not leg:
            raise HTTPException(status_code=404, detail="Leg not found")

        updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if k in _LEG_THRESHOLD_COLUMNS}
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE position_legs SET {set_clause} WHERE id = ?", (*updates.values(), leg_id)
            )
            audit.record(conn, "leg_thresholds_updated", {"position_id": position_id, "leg_id": leg_id, **updates})
        row = conn.execute("SELECT * FROM position_logs WHERE id = ?", (position_id,)).fetchone()
        return _row_to_position(conn, row)


def _close_leg(conn, position_id: int, leg, exit_price: float) -> None:
    """Closes one leg inside the caller's transaction and, if it was the last
    open leg, closes the position with the summed realized P&L."""
    now = datetime.now().isoformat()
    realized = leg_pnl(leg["action"], leg["entry_price"], exit_price, leg["qty"])
    # `AND closed_at IS NULL` makes the close idempotent: a concurrent or
    # repeated exit matches zero rows instead of booking P&L twice.
    cur = conn.execute(
        "UPDATE position_legs SET closed_at = ?, exit_price = ?, realized_pnl = ? WHERE id = ? AND closed_at IS NULL",
        (now, exit_price, realized, leg["id"]),
    )
    if cur.rowcount != 1:
        raise HTTPException(status_code=409, detail="Leg already closed")
    audit.record(
        conn,
        "leg_exited",
        {"position_id": position_id, "leg_id": leg["id"], "exit_price": exit_price, "realized_pnl": realized},
    )

    remaining_open = conn.execute(
        "SELECT COUNT(*) AS n FROM position_legs WHERE position_id = ? AND closed_at IS NULL", (position_id,)
    ).fetchone()["n"]
    if remaining_open == 0:
        realized_values = [
            r["realized_pnl"]
            for r in conn.execute("SELECT realized_pnl FROM position_legs WHERE position_id = ?", (position_id,))
        ]
        total_realized = sum_money(v or 0.0 for v in realized_values)
        conn.execute(
            "UPDATE position_logs SET status = 'closed', exit_time = ?, realized_pnl = ? WHERE id = ?",
            (now, total_realized, position_id),
        )
        audit.record(conn, "position_closed", {"position_id": position_id, "realized_pnl": total_realized})


@router.post("/{position_id}/legs/{leg_id}/exit", response_model=Position)
def exit_leg(position_id: int, leg_id: int, payload: LegExitRequest):
    """Logs a manual exit fill for one leg (qty/price the user actually got in
    the broker terminal) and books its realized P&L. If every leg on the
    position is now closed, the position itself flips to 'closed'."""
    with get_conn() as conn:
        leg = conn.execute(
            "SELECT * FROM position_legs WHERE id = ? AND position_id = ?", (leg_id, position_id)
        ).fetchone()
        if not leg:
            raise HTTPException(status_code=404, detail="Leg not found")
        if leg["closed_at"] is not None:
            raise HTTPException(status_code=409, detail="Leg already closed")
        _close_leg(conn, position_id, leg, payload.exit_price)
        row = conn.execute("SELECT * FROM position_logs WHERE id = ?", (position_id,)).fetchone()
        return _row_to_position(conn, row)


@router.post("/{position_id}/exit-all", response_model=Position)
def exit_all_legs(position_id: int, payload: LegExitRequest):
    """Bulk version of exit_leg — logs the same exit_price for every leg still
    open on the position. Still just a log entry, not an order. Runs in ONE
    transaction: either every open leg closes or none do."""
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM position_logs WHERE id = ?", (position_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Position not found")
        open_legs = conn.execute(
            "SELECT * FROM position_legs WHERE position_id = ? AND closed_at IS NULL", (position_id,)
        ).fetchall()
        if not open_legs:
            raise HTTPException(status_code=400, detail="No open legs on this position")
        for leg in open_legs:
            _close_leg(conn, position_id, leg, payload.exit_price)
        row = conn.execute("SELECT * FROM position_logs WHERE id = ?", (position_id,)).fetchone()
        return _row_to_position(conn, row)


@router.post("/refresh-ltp", response_model=List[Position], dependencies=[Depends(rate_limit(30, 60))])
async def refresh_ltp():
    """Best-effort live LTP refresh for every open leg, using the resolved
    Definedge option token (see integrations/instruments.py). No-ops
    per-leg on any lookup/auth failure so the UI still renders with the
    last known price when the broker session isn't active — mirrors the
    frontend's existing mock-data-fallback philosophy."""
    with get_conn() as conn:
        legs = conn.execute(
            """
            SELECT pl.id, pl.strike, pl.option_type, p.symbol, p.expiry
            FROM position_legs pl JOIN position_logs p ON p.id = pl.position_id
            WHERE pl.closed_at IS NULL
            """
        ).fetchall()

    if legs:
        try:
            await ensure_authenticated()
            rows = await instruments.get_master_rows(_client)
            for leg in legs:
                try:
                    expiry = date.fromisoformat(leg["expiry"])
                    contract = instruments.find_option(
                        rows, leg["symbol"], expiry, leg["strike"], leg["option_type"]
                    )
                    quote = await _client.get_quote(contract.exchange, contract.token)
                    ltp = float(quote["ltp"])
                    if not math.isfinite(ltp) or ltp < 0:
                        raise ValueError(f"implausible ltp {ltp!r}")
                    with get_conn() as conn:
                        conn.execute("UPDATE position_legs SET ltp = ? WHERE id = ?", (ltp, leg["id"]))
                except Exception as exc:  # noqa: BLE001 — best-effort per leg
                    logger.warning("refresh_ltp: skipping leg %s (%s: %s)", leg["id"], type(exc).__name__, exc)
        except DefinedgeAuthError:
            logger.info("refresh_ltp: no active Definedge session, skipping")

    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM position_logs ORDER BY entry_time DESC").fetchall()
        return [_row_to_position(conn, r) for r in rows]
