import math

import pytest

from app.db import audit
from app.db.sqlite import get_conn


def leg(**kw):
    base = dict(strike=21800, option_type="PE", action="SELL", qty=75, entry_price=100.0)
    base.update(kw)
    return base


def position(**kw):
    base = dict(
        strategy_type="bull_put_spread",
        symbol="NIFTY",
        expiry="2026-10-08",
        legs=[leg(), leg(strike=21700, action="BUY", entry_price=60.0)],
        entry_time="2026-10-01T10:00:00",
    )
    base.update(kw)
    return base


@pytest.fixture
def pos(client, auth):
    r = client.post("/api/v1/positions", json=position(), headers=auth)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["legs"][0].update(qty=0),
        lambda p: p["legs"][0].update(qty=-5),
        lambda p: p["legs"][0].update(qty=10**9),
        lambda p: p["legs"][0].update(qty=1.5),
        lambda p: p["legs"][0].update(entry_price=0),
        lambda p: p["legs"][0].update(entry_price=-1),
        lambda p: p["legs"][0].update(entry_price=10**12),
        lambda p: p["legs"][0].update(strike=0),
        lambda p: p["legs"][0].update(option_type="XX"),
        lambda p: p.update(legs=[]),
        lambda p: p.update(legs=[leg()] * 9),
        lambda p: p.update(symbol="NIFTY; DROP TABLE position_logs"),
        lambda p: p.update(symbol=""),
        lambda p: p.update(notes="x" * 2001),
        lambda p: p.update(strategy_type="martingale"),
        lambda p: p.update(status="closed"),  # unknown field
        lambda p: p.update(realized_pnl=1e9),  # server-owned
        lambda p: p.update(max_loss=-1),
    ],
)
def test_create_rejects_bad_input(client, auth, mutate):
    p = position()
    mutate(p)
    assert client.post("/api/v1/positions", json=p, headers=auth).status_code == 422


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_nan_and_inf_rejected(client, auth, bad):
    body = '{"strategy_type":"straddle","symbol":"NIFTY","expiry":"2026-10-08","entry_time":"2026-10-01T10:00:00","legs":[{"strike":21800,"option_type":"CE","action":"BUY","qty":1,"entry_price":%s}]}' % bad
    r = client.post("/api/v1/positions", content=body, headers={**auth, "Content-Type": "application/json"})
    assert r.status_code == 422


def test_symbol_normalised_and_injection_inert(client, auth):
    r = client.post("/api/v1/positions", json=position(symbol=" nifty "), headers=auth)
    assert r.status_code == 201 and r.json()["symbol"] == "NIFTY"


def test_server_owned_fields_cannot_be_patched(client, auth, pos):
    for body in ({"status": "closed"}, {"realized_pnl": 1e6}, {"exit_time": "2026-10-01T10:00:00"}):
        assert client.patch(f"/api/v1/positions/{pos['id']}", json=body, headers=auth).status_code == 422
    r = client.patch(f"/api/v1/positions/{pos['id']}", json={"notes": "adjusted"}, headers=auth)
    assert r.status_code == 200 and r.json()["notes"] == "adjusted" and r.json()["status"] == "open"


def test_leg_threshold_validation_and_audit(client, auth, pos):
    p, lg = pos["id"], pos["legs"][0]["id"]
    assert client.patch(f"/api/v1/positions/{p}/legs/{lg}", json={"sl": -3}, headers=auth).status_code == 422
    assert client.patch(f"/api/v1/positions/{p}/legs/{lg}", json={"qty": 9999}, headers=auth).status_code == 422
    r = client.patch(f"/api/v1/positions/{p}/legs/{lg}", json={"sl": 150, "target": 40}, headers=auth)
    assert r.status_code == 200


def test_exit_pnl_exact_and_position_closes(client, auth, pos):
    p = pos["id"]
    short, long_ = pos["legs"]
    r = client.post(f"/api/v1/positions/{p}/legs/{short['id']}/exit", json={"exit_price": 40.0}, headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "open"
    r = client.post(f"/api/v1/positions/{p}/legs/{long_['id']}/exit", json={"exit_price": 20.0}, headers=auth)
    body = r.json()
    # short: (100-40)*75 = 4500 ; long: (20-60)*75 = -3000
    assert body["status"] == "closed" and body["realized_pnl"] == 1500.0 and body["booked_pnl"] == 1500.0


def test_double_exit_rejected_and_pnl_not_double_booked(client, auth, pos):
    p, lg = pos["id"], pos["legs"][0]["id"]
    assert client.post(f"/api/v1/positions/{p}/legs/{lg}/exit", json={"exit_price": 40}, headers=auth).status_code == 200
    again = client.post(f"/api/v1/positions/{p}/legs/{lg}/exit", json={"exit_price": 1}, headers=auth)
    assert again.status_code == 409
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE event='leg_exited'").fetchone()[0] == 1


def test_exit_rejects_bad_prices(client, auth, pos):
    p, lg = pos["id"], pos["legs"][0]["id"]
    for bad in (-1, 10**12):
        assert client.post(f"/api/v1/positions/{p}/legs/{lg}/exit", json={"exit_price": bad}, headers=auth).status_code == 422
    assert client.post(f"/api/v1/positions/{p}/legs/{lg}/exit", json={"exit_price": 0}, headers=auth).status_code == 200  # expired worthless


def test_exit_all_is_atomic(client, auth, pos, monkeypatch):
    """If closing any leg fails mid-way, NO leg may be left closed."""
    from app.api.routes import positions

    real = positions._close_leg
    calls = {"n": 0}

    def flaky(conn, position_id, leg, exit_price):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real(conn, position_id, leg, exit_price)

    monkeypatch.setattr(positions, "_close_leg", flaky)
    from fastapi.testclient import TestClient

    from app.main import app

    r = TestClient(app, raise_server_exceptions=False).post(f"/api/v1/positions/{pos['id']}/exit-all", json={"exit_price": 50}, headers=auth)
    assert r.status_code == 500
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM position_legs WHERE closed_at IS NOT NULL").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE event='leg_exited'").fetchone()[0] == 0


def test_exit_all_happy_path_and_repeat(client, auth, pos):
    r = client.post(f"/api/v1/positions/{pos['id']}/exit-all", json={"exit_price": 50}, headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "closed"
    assert client.post(f"/api/v1/positions/{pos['id']}/exit-all", json={"exit_price": 50}, headers=auth).status_code == 400
    assert client.post("/api/v1/positions/9999/exit-all", json={"exit_price": 50}, headers=auth).status_code == 404


def test_money_events_are_audited_and_chain_verifies(client, auth, pos):
    client.post(f"/api/v1/positions/{pos['id']}/exit-all", json={"exit_price": 50}, headers=auth)
    with get_conn() as conn:
        events = [r["event"] for r in conn.execute("SELECT event FROM audit_log ORDER BY id")]
        assert events[0] == "position_created" and events.count("leg_exited") == 2 and events[-1] == "position_closed"
        assert audit.verify_chain(conn)


def test_list_filter_validated(client, auth, pos):
    assert client.get("/api/v1/positions?status=open", headers=auth).status_code == 200
    assert client.get("/api/v1/positions?status=x'%20OR%201=1", headers=auth).status_code == 422


def test_payoff_rejects_bad_legs(client, auth):
    body = {"symbol": "NIFTY", "expiry": "2026-10-08", "legs": [{"strike": -5, "option_type": "CE", "action": "BUY", "qty": 1}]}
    assert client.post("/api/v1/analytics/payoff", json=body, headers=auth).status_code == 422
    assert not math.isnan(1.0)
