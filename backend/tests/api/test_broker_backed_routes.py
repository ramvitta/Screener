"""Routes that talk to the broker, with the client mocked."""

import pytest

from app.api.routes import account, analytics, chain, market, positions
from app.integrations.definedge_client import DefinedgeAuthError
from tests.unit.test_technicals_instruments import ROWS


class FakeClient:
    def __init__(self, ltp=21800.0, limits=None):
        self.ltp, self.limits = ltp, limits or {}

    async def get_quote(self, exchange, token):
        return {"ltp": str(self.ltp), "day_open": str(self.ltp - 10)}

    async def get_limits(self):
        return self.limits

    async def get_historical_candles(self, *a, **k):
        return []


async def _ok(force=False):
    return None


async def _rows(client):
    return ROWS


@pytest.fixture
def broker(monkeypatch):
    fake = FakeClient(limits={"cash": "1000.5", "accountid": "ACT5678", "uid": "UID1234", "marginused": "5"})
    for mod in (account, analytics, chain, market, positions):
        monkeypatch.setattr(mod, "_client", fake)
        monkeypatch.setattr(mod, "ensure_authenticated", _ok)
    monkeypatch.setattr("app.integrations.instruments.get_master_rows", _rows)
    return fake


def test_margin_is_allowlisted(client, auth, broker):
    body = client.get("/api/v1/account/margin", headers=auth).json()
    assert body == {"cash": "1000.5", "marginused": "5"}
    assert "ACT5678" not in str(body)


def test_broker_unavailable_is_503_not_401(client, auth, monkeypatch):
    async def down(force=False):
        raise DefinedgeAuthError("x")

    for mod in (account, analytics, chain, market):
        monkeypatch.setattr(mod, "ensure_authenticated", down)
    for path in ("/api/v1/account/margin", "/api/v1/market/indices", "/api/v1/chain/NIFTY"):
        assert client.get(path, headers=auth).status_code == 503
    body = {"symbol": "NIFTY", "expiry": "2026-10-08", "legs": [{"strike": 21800, "option_type": "CE", "action": "BUY", "qty": 1, "entry_price": 50}]}
    assert client.post("/api/v1/analytics/payoff", json=body, headers=auth).status_code == 503


def test_index_quote(client, auth, broker):
    assert client.get("/api/v1/market/indices", headers=auth).json()[0]["spot"] == 21800.0


def test_payoff_math(client, auth, broker):
    body = {
        "symbol": "NIFTY",
        "expiry": "2099-10-08",
        "legs": [
            {"strike": 21800, "option_type": "PE", "action": "SELL", "qty": 75, "entry_price": 100, "iv": 0.15},
            {"strike": 21700, "option_type": "PE", "action": "BUY", "qty": 75, "entry_price": 60, "iv": 0.15},
        ],
    }
    r = client.post("/api/v1/analytics/payoff", json=body, headers=auth).json()
    # credit spread: max profit = credit*qty, max loss = (width - credit)*qty
    assert r["max_profit"] == pytest.approx(40 * 75)
    assert r["max_loss"] == pytest.approx(-(100 - 40) * 75)
    assert r["breakevens"] and abs(r["breakevens"][0] - 21760) < 5


def test_payoff_rejects_implausible_spot(client, auth, broker):
    broker.ltp = 0.0
    body = {"symbol": "NIFTY", "expiry": "2099-10-08", "legs": [{"strike": 21800, "option_type": "CE", "action": "BUY", "qty": 1, "entry_price": 5}]}
    assert client.post("/api/v1/analytics/payoff", json=body, headers=auth).status_code == 502


def test_chain_symbol_validated(client, auth, broker):
    assert client.get("/api/v1/chain/NIFTY%3B--", headers=auth).status_code == 422


def test_refresh_ltp_updates_and_survives_bad_quote(client, auth, broker):
    pos = {
        "strategy_type": "straddle", "symbol": "NIFTY", "expiry": "2026-10-08", "entry_time": "2026-10-01T10:00:00",
        "legs": [{"strike": 21800, "option_type": "CE", "action": "BUY", "qty": 75, "entry_price": 50}],
    }
    assert client.post("/api/v1/positions", json=pos, headers=auth).status_code == 201
    broker.ltp = 77.5
    r = client.post("/api/v1/positions/refresh-ltp", headers=auth).json()
    assert r[0]["legs"][0]["ltp"] == 77.5 and r[0]["legs"][0]["unrealized_pnl"] == 2062.5
    broker.ltp = float("nan")  # implausible quote must not overwrite the last good LTP
    r = client.post("/api/v1/positions/refresh-ltp", headers=auth).json()
    assert r[0]["legs"][0]["ltp"] == 77.5
