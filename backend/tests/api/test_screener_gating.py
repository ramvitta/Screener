from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.workers import screener_loop as loop


@pytest.fixture(autouse=True)
def reset_loop(monkeypatch):
    monkeypatch.setattr(loop, "_latest_signals", [])
    monkeypatch.setattr(loop, "_last_success_at", None)
    monkeypatch.setattr(loop, "_load_chain_from_clickhouse", lambda symbol="NIFTY": None)


async def _no_live(symbol="NIFTY"):
    return None


async def test_no_real_data_means_no_signals_and_degraded_status(monkeypatch, client, auth):
    monkeypatch.setattr(loop, "_live_snapshot_from_definedge", _no_live)
    await loop.run_screener_job()
    assert client.get("/api/v1/screener/signals", headers=auth).json() == []
    status = client.get("/api/v1/screener/status", headers=auth).json()
    assert status["state"] == "degraded" and "sample data is disabled" in status["message"]
    from app.db.sqlite import get_conn

    with get_conn() as conn:  # and no alerts were fabricated either
        assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0


async def test_sample_data_only_when_explicitly_allowed_and_labelled(monkeypatch, client, auth):
    monkeypatch.setattr(loop, "_live_snapshot_from_definedge", _no_live)
    monkeypatch.setattr(settings, "allow_sample_data", True)
    await loop.run_screener_job()
    signals = client.get("/api/v1/screener/signals", headers=auth).json()
    assert signals and {s["data_source"] for s in signals} == {"sample"}
    assert client.get("/api/v1/screener/status", headers=auth).json()["state"] == "sample"


async def test_live_signals_labelled_live(monkeypatch, client, auth):
    from app.models.schemas import DataSource

    async def live(symbol="NIFTY"):
        snap = loop._sample_snapshot()
        snap.data_source = DataSource.live
        snap.historical_iv = []
        return snap

    monkeypatch.setattr(loop, "_live_snapshot_from_definedge", live)
    await loop.run_screener_job()
    signals = client.get("/api/v1/screener/signals", headers=auth).json()
    assert signals and {s["data_source"] for s in signals} == {"live"}
    assert client.get("/api/v1/screener/status", headers=auth).json()["state"] == "ok"


async def test_stale_signals_are_withheld(monkeypatch):
    monkeypatch.setattr(loop, "_latest_signals", [{"signal_id": "x"}])
    monkeypatch.setattr(loop, "_last_success_at", datetime.now(timezone.utc) - timedelta(seconds=settings.max_signal_age_seconds + 5))
    assert loop.get_latest_signals() == []
    monkeypatch.setattr(loop, "_last_success_at", datetime.now(timezone.utc))
    assert loop.get_latest_signals() == [{"signal_id": "x"}]


async def test_broker_down_degrades_instead_of_fabricating(monkeypatch, client, auth):
    """Failure injection: the live path raises -> still no fake signals."""
    from app.integrations.definedge_client import DefinedgeAuthError

    async def ensure(force=False):
        raise DefinedgeAuthError("down")

    import app.api.routes.auth as auth_routes

    monkeypatch.setattr(auth_routes, "ensure_authenticated", ensure)
    await loop.run_screener_job()
    assert client.get("/api/v1/screener/signals", headers=auth).json() == []
    assert client.get("/api/v1/screener/status", headers=auth).json()["state"] == "degraded"
