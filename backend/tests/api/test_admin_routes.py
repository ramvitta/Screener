"""API coverage for the admin history routes (admin.py): the retention prune
and the SQLite-backed download-job readers. Network paths (fno-stocks,
validate, create_job, coverage) are exercised through mocks where practical.
"""

from datetime import date, timedelta

from app.config import settings
from app.db.sqlite import get_conn


def _seed_job(conn, job_id=None, symbol="RELIANCE", status="pending"):
    cur = conn.execute(
        "INSERT INTO download_jobs (symbol, start_date, end_date, scope_json, status) VALUES (?, ?, ?, ?, ?)",
        (symbol, "2025-01-01", "2025-01-05", '{"equity":true,"futures":true,"options":true,"strike_band":10}', status),
    )
    return job_id or cur.lastrowid


def _seed_item(conn, job_id, key="EQUITY|RELIANCE", status="done", rows=10):
    conn.execute(
        "INSERT INTO download_job_items (job_id, instrument_key, status, rows) VALUES (?, ?, ?, ?)",
        (job_id, key, status, rows),
    )


def _seed_run_and_signal(conn, run_id, started_at, signal_id="sig"):
    conn.execute(
        "INSERT INTO screener_runs (run_id, started_at, status, signal_count) VALUES (?, ?, 'ok', 1)",
        (run_id, started_at),
    )
    conn.execute(
        "INSERT INTO screener_signals (run_id, signal_id, strategy_type, symbol, status, generated_at, decision_json) VALUES (?, ?, 'bull_put_spread', 'NIFTY', 'accepted', ?, '{}')",
        (run_id, signal_id, started_at),
    )


def test_prune_signals_requires_confirm(client, auth):
    r = client.post("/api/v1/admin/history/prune-signals", headers=auth, json={"confirm": False})
    assert r.status_code == 400


def test_prune_signals_deletes_old_keeps_new(client, auth):
    with get_conn() as conn:
        _seed_run_and_signal(conn, "old-run", "2020-01-01T00:00:00+00:00")
        _seed_run_and_signal(conn, "new-run", "2026-01-01T00:00:00+00:00", signal_id="sig2")
    r = client.post("/api/v1/admin/history/prune-signals", headers=auth, json={"confirm": True, "before": "2025-01-01"})
    assert r.status_code == 200
    body = r.json()
    assert body["deleted_runs"] == 1 and body["deleted_signals"] == 1
    assert body["cutoff"] == "2025-01-01"
    with get_conn() as conn:
        remaining = conn.execute("SELECT run_id FROM screener_runs ORDER BY run_id").fetchall()
        assert [x["run_id"] for x in remaining] == ["new-run"]


def test_prune_signals_default_cutoff_from_retention(client, auth):
    with get_conn() as conn:
        _seed_run_and_signal(conn, "ancient", "2020-01-01T00:00:00+00:00")
    r = client.post("/api/v1/admin/history/prune-signals", headers=auth, json={"confirm": True})
    assert r.status_code == 200
    assert r.json()["deleted_runs"] == 1
    expected = (date.today() - timedelta(days=settings.signal_history_retention_days)).isoformat()
    assert r.json()["cutoff"] == expected


def test_list_jobs_and_get_job(client, auth):
    with get_conn() as conn:
        jid = _seed_job(conn)
    r = client.get("/api/v1/admin/history/jobs", headers=auth)
    assert r.status_code == 200
    assert r.json()[0]["id"] == jid
    r = client.get(f"/api/v1/admin/history/jobs/{jid}", headers=auth)
    assert r.status_code == 200
    assert r.json()["symbol"] == "RELIANCE"
    assert client.get("/api/v1/admin/history/jobs/9999", headers=auth).status_code == 404


def test_get_job_items_and_404(client, auth):
    with get_conn() as conn:
        jid = _seed_job(conn)
        _seed_item(conn, jid)
    r = client.get(f"/api/v1/admin/history/jobs/{jid}/items", headers=auth)
    assert r.status_code == 200
    assert r.json()[0]["instrument_key"] == "EQUITY|RELIANCE"
    assert client.get("/api/v1/admin/history/jobs/9999/items", headers=auth).status_code == 404


def test_cancel_job_and_404(client, auth):
    with get_conn() as conn:
        jid = _seed_job(conn)
    r = client.post(f"/api/v1/admin/history/jobs/{jid}/stop", headers=auth)
    assert r.status_code == 200
    assert r.json()["id"] == jid
    assert client.post("/api/v1/admin/history/jobs/9999/stop", headers=auth).status_code == 404
