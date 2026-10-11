import pytest
from starlette.routing import Route
from starlette.websockets import WebSocketDisconnect

from app.config import settings

OPEN_PATHS = {"/api/v1/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


def _concrete(path: str) -> str:
    import re

    return re.sub(r"\{[^}]+\}", "1", path)


def _routes():
    from app.main import app

    for r in app.routes:
        if isinstance(r, Route) and r.path not in OPEN_PATHS:
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                yield m, _concrete(r.path)


def test_every_route_rejects_missing_token(client):
    checked = 0
    for method, path in _routes():
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} -> {resp.status_code} (expected 401)"
        checked += 1
    assert checked > 25, "route discovery looks broken"


@pytest.mark.parametrize("header", ["Bearer wrong", "Basic abc", "Bearer ", "", "bearer"])
def test_bad_tokens_rejected(client, header):
    assert client.get("/api/v1/positions", headers={"Authorization": header}).status_code == 401


def test_valid_token_accepted(client, auth):
    assert client.get("/api/v1/positions", headers=auth).status_code == 200


def test_health_is_open_and_minimal(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_unconfigured_token_fails_closed(client, monkeypatch):
    monkeypatch.setattr(settings, "api_auth_token", "")
    assert client.get("/api/v1/positions", headers={"Authorization": "Bearer "}).status_code == 401
    assert client.get("/api/v1/positions", headers={"Authorization": "Bearer anything"}).status_code == 401


def test_cors_is_restricted(client):
    r = client.options(
        "/api/v1/positions",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in r.headers
    ok = client.options(
        "/api/v1/positions",
        headers={"Origin": "http://localhost:3001", "Access-Control-Request-Method": "DELETE"},
    )
    assert "DELETE" not in ok.headers.get("access-control-allow-methods", "")


def test_response_headers(client, auth):
    r = client.get("/api/v1/positions", headers=auth)
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-request-id"]


def test_websockets_require_token_and_origin(client, auth):
    for path in ("/ws/alerts", "/ws/quotes"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path):
                pass
        with pytest.raises(WebSocketDisconnect):  # right token, hostile origin
            with client.websocket_connect(path, headers={**auth, "Origin": "http://evil.example"}):
                pass
        with pytest.raises(WebSocketDisconnect):  # wrong token via subprotocol
            with client.websocket_connect(path, subprotocols=["bearer.nope"]):
                pass
    with client.websocket_connect("/ws/quotes", headers=auth) as ws:  # header auth
        assert ws.receive_json() == {"type": "heartbeat"}
    with client.websocket_connect(
        "/ws/quotes", subprotocols=[f"bearer.{settings.api_auth_token}"], headers={"Origin": "http://localhost:3001"}
    ) as ws:  # browser-style subprotocol auth
        assert ws.receive_json() == {"type": "heartbeat"}


def test_sensitive_routes_are_rate_limited(client, auth, monkeypatch):
    from app.api.routes import auth as auth_routes

    async def boom(force=False):
        raise auth_routes.DefinedgeAuthError("nope")

    monkeypatch.setattr(auth_routes, "ensure_authenticated", boom)
    codes = [client.post("/api/v1/auth/definedge/relogin", headers=auth).status_code for _ in range(12)]
    assert codes[:10] == [502] * 10 and codes[10:] == [429, 429]


def test_error_bodies_do_not_leak_upstream_text(client, auth, monkeypatch):
    from app.api.routes import auth as auth_routes

    async def boom(force=False):
        raise auth_routes.DefinedgeAuthError("https://x/login/SECRET-TOKEN-IN-URL failed")

    monkeypatch.setattr(auth_routes, "ensure_authenticated", boom)
    r = client.post("/api/v1/auth/definedge/relogin", headers=auth)
    assert "SECRET-TOKEN" not in r.text and r.json() == {"detail": "Broker login failed"}


def test_session_endpoint_never_returns_account_ids(client, auth):
    from app.db.sqlite import get_conn

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO broker_sessions (uid, actid, api_session_key, ws_session_key, issued_at, expires_at) VALUES ('UID1234','ACT5678','k','k','2026-01-01','2099-01-01')"
        )
    body = client.get("/api/v1/auth/definedge/session", headers=auth).text
    assert "UID1234" not in body and "ACT5678" not in body


def test_purge_removes_legacy_plaintext_sessions_immediately():
    from datetime import datetime, timedelta

    from app.api.routes.auth import purge_expired_sessions
    from app.db.sqlite import get_conn

    now = datetime.now()
    with get_conn() as conn:
        for key, active, expires in (("plain-old-key", 0, now + timedelta(hours=1)), ("enc:abc", 1, now + timedelta(hours=1)), ("", 0, now - timedelta(days=3))):
            conn.execute(
                "INSERT INTO broker_sessions (uid, actid, api_session_key, ws_session_key, issued_at, expires_at, is_active) VALUES ('U','A',?,?,?,?,?)",
                (key, key, (now - timedelta(days=3)).isoformat() if not active else now.isoformat(), expires.isoformat(), active),
            )
    assert purge_expired_sessions() == 2  # the plaintext row + the old blank expired row
    with get_conn() as conn:
        assert [r["api_session_key"] for r in conn.execute("SELECT api_session_key FROM broker_sessions")] == ["enc:abc"]
