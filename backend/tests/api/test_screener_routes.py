"""API coverage for the screener signal-history routes (screener.py).

These endpoints are thin SQLite-backed readers/writers over the
screener_runs / screener_signals / screener_rules tables. Seeding rows
directly exercises every branch (found / not-found / filters / update vs
insert) without touching Definedge or ClickHouse.
"""

import json
from datetime import datetime, timezone

from app.db.sqlite import get_conn
from app.workers import screener_loop as loop


def _seed_run(conn, run_id="run-1", started_at="2025-01-01T00:00:00+00:00", data_source="live", status="ok", message="", signal_count=1):
    conn.execute(
        "INSERT INTO screener_runs (run_id, started_at, data_source, status, message, signal_count) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, started_at, data_source, status, message, signal_count),
    )


def _seed_signal(conn, *, run_id="run-1", signal_id="sig-1", strategy_type="bull_put_spread", symbol="NIFTY", expiry="2025-01-09", status="accepted", reject_reason=None, generated_at="2025-01-01T00:00:00+00:00", data_source="live", reason=None, reason_source=None, signal_json=None, decision_json=None):
    conn.execute(
        """
        INSERT INTO screener_signals
        (run_id, signal_id, strategy_type, symbol, expiry, status, reject_reason, generated_at, data_source, reason, reason_source, signal_json, decision_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, signal_id, strategy_type, symbol, expiry, status, reject_reason, generated_at, data_source, reason, reason_source, signal_json, decision_json or json.dumps({})),
    )


def test_latest_signals_endpoint(client, auth, monkeypatch):
    monkeypatch.setattr(loop, "_latest_signals", [{"signal_id": "s1", "strategy_type": "bull_put_spread"}])
    monkeypatch.setattr(loop, "_last_success_at", datetime.now(timezone.utc))
    r = client.get("/api/v1/screener/signals", headers=auth)
    assert r.status_code == 200
    assert r.json() == [{"signal_id": "s1", "strategy_type": "bull_put_spread"}]


def test_status_endpoint(client, auth, monkeypatch):
    monkeypatch.setattr(loop, "_status", {"state": "ok", "data_source": "live", "message": "hi", "last_run": None})
    r = client.get("/api/v1/screener/status", headers=auth)
    assert r.status_code == 200
    assert r.json()["state"] == "ok"


def test_list_rules_empty(client, auth):
    assert client.get("/api/v1/screener/rules", headers=auth).json() == []


def test_upsert_rule_insert_and_list(client, auth):
    payload = {"strategy_type": "bull_put_spread", "symbol": "NIFTY", "is_active": True, "params": {"min_iv_rank": 0.3}}
    r = client.post("/api/v1/screener/rules", headers=auth, json=payload)
    assert r.status_code == 201
    rule_id = r.json()["id"]
    assert rule_id is not None
    listed = client.get("/api/v1/screener/rules", headers=auth).json()
    assert listed[0]["id"] == rule_id
    assert listed[0]["params"] == {"min_iv_rank": 0.3}


def test_upsert_rule_update_existing(client, auth):
    r = client.post("/api/v1/screener/rules", headers=auth, json={"strategy_type": "bull_put_spread", "symbol": "NIFTY", "is_active": True, "params": {"min_iv_rank": 0.3}})
    rule_id = r.json()["id"]
    r = client.post("/api/v1/screener/rules", headers=auth, json={"id": rule_id, "strategy_type": "bull_put_spread", "symbol": "NIFTY", "is_active": False, "params": {"min_iv_rank": 0.5}})
    assert r.status_code == 201
    assert r.json()["is_active"] is False
    assert r.json()["params"] == {"min_iv_rank": 0.5}


def test_upsert_rule_update_missing_404(client, auth):
    r = client.post("/api/v1/screener/rules", headers=auth, json={"id": 999, "strategy_type": "bull_put_spread", "symbol": "NIFTY", "is_active": True, "params": {}})
    assert r.status_code == 404


def test_list_runs(client, auth):
    with get_conn() as conn:
        _seed_run(conn, run_id="run-1")
    r = client.get("/api/v1/screener/runs", headers=auth)
    assert r.status_code == 200
    assert r.json()[0]["run_id"] == "run-1"


def test_get_run_found_with_signals(client, auth):
    with get_conn() as conn:
        _seed_run(conn, run_id="run-1", signal_count=1)
        _seed_signal(conn, run_id="run-1", decision_json=json.dumps({"computed": {"pop": 0.8}}), signal_json=json.dumps({"legs": []}))
    r = client.get("/api/v1/screener/runs/run-1", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == "run-1"
    assert len(body["signals"]) == 1
    assert body["signals"][0]["decision_json"]["computed"]["pop"] == 0.8


def test_get_run_404(client, auth):
    assert client.get("/api/v1/screener/runs/nope", headers=auth).status_code == 404


def test_list_history_with_filters(client, auth):
    with get_conn() as conn:
        _seed_run(conn, run_id="r1")
        _seed_run(conn, run_id="r2")
        _seed_signal(conn, run_id="r1", signal_id="s1", strategy_type="bull_put_spread", symbol="NIFTY", status="accepted")
        _seed_signal(conn, run_id="r2", signal_id="s2", strategy_type="iron_condor", symbol="BANKNIFTY", status="rejected", reject_reason="wing_missing")
    r = client.get("/api/v1/screener/history", headers=auth)
    assert r.status_code == 200
    assert len(r.json()) == 2
    r = client.get("/api/v1/screener/history", headers=auth, params={"status": "rejected"})
    assert len(r.json()) == 1 and r.json()[0]["signal_id"] == "s2"
    r = client.get("/api/v1/screener/history", headers=auth, params={"strategy_type": "bull_put_spread"})
    assert len(r.json()) == 1 and r.json()[0]["signal_id"] == "s1"
    r = client.get("/api/v1/screener/history", headers=auth, params={"symbol": "nifty"})
    assert len(r.json()) == 1 and r.json()[0]["symbol"] == "NIFTY"


def test_get_history_found_and_404(client, auth):
    with get_conn() as conn:
        _seed_run(conn, run_id="r1")
        _seed_signal(conn, run_id="r1", signal_id="s1", symbol="NIFTY")
        sid = conn.execute("SELECT id FROM screener_signals WHERE signal_id = 's1'").fetchone()["id"]
    r = client.get(f"/api/v1/screener/history/{sid}", headers=auth)
    assert r.status_code == 200
    assert r.json()["signal_id"] == "s1"
    assert client.get("/api/v1/screener/history/99999", headers=auth).status_code == 404
