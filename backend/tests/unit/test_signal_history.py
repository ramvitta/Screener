"""Signal-history persistence: the screener_runs/screener_signals schema and
the decision_json round-trip. (The run_screener_job() integration itself is
covered by tests/api/test_screener_gating.py.)"""

import json

from app.db.sqlite import get_conn


def test_screener_history_schema_exists():
    with get_conn() as conn:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"screener_runs", "screener_signals"} <= tables


def test_run_and_accepted_signal_round_trip():
    run_id = "run-abc-123"
    decision = {"params": {"min_iv_rank": 0.0, "min_pop": 0.6}, "computed": {"pop": 0.8}, "reject_reason": None}
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO screener_runs (run_id, started_at, data_source, status, message, signal_count) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, "2025-01-01T00:00:00+00:00", "sample", "ok", "1 signal", 1),
        )
        conn.execute(
            """
            INSERT INTO screener_signals
            (run_id, signal_id, strategy_type, symbol, expiry, status, reject_reason, generated_at, data_source, reason, reason_source, signal_json, decision_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, "sig-1", "bull_put_spread", "NIFTY", "2025-01-09", "accepted", None,
             "2025-01-01T00:00:00+00:00", "sample", "reason text", "template", json.dumps({"x": 1}), json.dumps(decision)),
        )
    with get_conn() as conn:
        run = conn.execute("SELECT * FROM screener_runs WHERE run_id = ?", (run_id,)).fetchone()
        assert run["status"] == "ok" and run["signal_count"] == 1
        sig = conn.execute("SELECT * FROM screener_signals WHERE run_id = ?", (run_id,)).fetchone()
        assert sig["status"] == "accepted" and sig["signal_id"] == "sig-1"
        assert json.loads(sig["decision_json"])["computed"]["pop"] == 0.8


def test_rejected_signal_stores_reason_and_decision():
    run_id = "run-rej-1"
    decision = {"params": {}, "computed": {"pop": None}, "reject_reason": "wing_missing"}
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO screener_runs (run_id, started_at, status, message, signal_count) VALUES (?, ?, 'ok', '0 signals', 0)",
            (run_id, "2025-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO screener_signals (run_id, strategy_type, symbol, status, reject_reason, generated_at, decision_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, "iron_condor", "NIFTY", "rejected", "wing_missing", "2025-01-01T00:00:00+00:00", json.dumps(decision)),
        )
    with get_conn() as conn:
        sig = conn.execute("SELECT * FROM screener_signals WHERE run_id = ?", (run_id,)).fetchone()
        assert sig["reject_reason"] == "wing_missing"
        assert json.loads(sig["decision_json"])["reject_reason"] == "wing_missing"


def test_empty_run_recorded_with_zero_signals():
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO screener_runs (run_id, started_at, status, message, signal_count) VALUES ('run-empty', '2025-01-01T00:00:00+00:00', 'degraded', 'no data', 0)"
        )
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM screener_signals WHERE run_id = 'run-empty'").fetchone()[0] == 0
        run = conn.execute("SELECT * FROM screener_runs WHERE run_id = 'run-empty'").fetchone()
        assert run["status"] == "degraded" and run["signal_count"] == 0
