import asyncio
from datetime import datetime, timedelta

from app.api.routes import auth
from app.integrations.definedge_client import DefinedgeSession


async def test_concurrent_ensure_authenticated_logs_in_once(monkeypatch):
    """Definedge revokes older tokens on a newer login, so parallel callers
    (startup, scheduler, routes) must share ONE login."""
    logins = {"n": 0}

    async def slow_login():
        logins["n"] += 1
        await asyncio.sleep(0.05)
        s = DefinedgeSession("U1234567", "A1234567", "api-key-xyz-1", "ws-key-xyz-1", datetime.now() + timedelta(hours=12))
        auth._client._session = s
        return s

    monkeypatch.setattr(auth._client, "login", slow_login)
    monkeypatch.setattr(auth._client, "_session", None)
    results = await asyncio.gather(*[auth.ensure_authenticated() for _ in range(8)])
    assert logins["n"] == 1
    assert all(r is results[0] for r in results)


async def test_forced_logins_are_serialised(monkeypatch):
    active = {"now": 0, "max": 0}

    async def login():
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.02)
        active["now"] -= 1
        return DefinedgeSession("U1234567", "A1234567", "api-key-xyz-2", "ws-key-xyz-2", datetime.now() + timedelta(hours=12))

    monkeypatch.setattr(auth._client, "login", login)
    await asyncio.gather(*[auth.ensure_authenticated(force=True) for _ in range(4)])
    assert active["max"] == 1
