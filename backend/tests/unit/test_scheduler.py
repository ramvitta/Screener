"""Unit coverage for the APScheduler job definitions in scheduler.py —
the job bodies are exercised directly (they're thin try/except wrappers),
and start/stop are covered by stubbing the scheduler's add_job/start/shutdown.
"""

from app.integrations.definedge_client import DefinedgeAuthError
from app.workers import scheduler as sched


async def test_reauth_job_success(monkeypatch):
    called = {}

    async def ensure():
        called["ok"] = True

    import app.api.routes.auth as auth_routes

    monkeypatch.setattr(auth_routes, "ensure_authenticated", ensure)
    await sched._reauth_job()
    assert called.get("ok") is True


async def test_reauth_job_swallows_auth_error(monkeypatch):
    async def ensure():
        raise DefinedgeAuthError("down")

    import app.api.routes.auth as auth_routes

    monkeypatch.setattr(auth_routes, "ensure_authenticated", ensure)
    await sched._reauth_job()  # must not raise


async def test_retention_job_purges(monkeypatch):
    import app.api.routes.auth as auth_routes

    monkeypatch.setattr(auth_routes, "purge_expired_sessions", lambda: 3)
    await sched._retention_job()  # must not raise


async def test_retention_job_swallows_error(monkeypatch):
    import app.api.routes.auth as auth_routes

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(auth_routes, "purge_expired_sessions", boom)
    await sched._retention_job()  # must not raise


def test_start_and_stop_scheduler(monkeypatch):
    added = []

    class FakeScheduler:
        def add_job(self, fn, *a, **kw):
            added.append((fn, kw.get("id")))

        def start(self):
            pass

        def shutdown(self, wait=False):
            pass

    fake = FakeScheduler()
    monkeypatch.setattr(sched, "scheduler", fake)
    sched.start_scheduler()
    assert [job_id for _, job_id in added] == ["screener_run", "definedge_reauth", "retention"]
    sched.stop_scheduler()  # must not raise
