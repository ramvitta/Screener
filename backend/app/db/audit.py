"""Append-only, hash-chained audit log for every money event.

Each row stores sha256(prev_hash + event + payload + ts). Triggers in
db/sqlite.py reject UPDATE and DELETE, and verify_chain() detects any row
that was altered out-of-band.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict

GENESIS = "0" * 64


def _digest(prev_hash: str, event: str, payload_json: str, ts: str) -> str:
    return hashlib.sha256(f"{prev_hash}|{event}|{payload_json}|{ts}".encode("utf-8")).hexdigest()


def record(conn, event: str, payload: Dict[str, Any]) -> None:
    """Writes inside the caller's transaction, so the audit row commits or
    rolls back together with the money change it describes."""
    row = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    prev_hash = row["hash"] if row else GENESIS
    payload_json = json.dumps(payload, sort_keys=True, default=str)
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO audit_log (ts, event, payload_json, prev_hash, hash) VALUES (?, ?, ?, ?, ?)",
        (ts, event, payload_json, prev_hash, _digest(prev_hash, event, payload_json, ts)),
    )


def verify_chain(conn) -> bool:
    prev = GENESIS
    for row in conn.execute("SELECT * FROM audit_log ORDER BY id"):
        if row["prev_hash"] != prev or row["hash"] != _digest(prev, row["event"], row["payload_json"], row["ts"]):
            return False
        prev = row["hash"]
    return True
