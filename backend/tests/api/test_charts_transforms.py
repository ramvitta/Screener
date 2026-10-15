"""Endpoint tests for POST /api/v1/charts/transforms (shape, box defaults, 400s)."""

from datetime import datetime, timedelta

import pytest

from app.api.routes import charts
from app.integrations import chart_history
from app.integrations.definedge_client import Candle

# Future + a 21800 CE/PE and 21700 PE so a 2-leg bull put spread resolves.
ROWS = [
    {"segment": "NFO", "instrument_type": "FUTIDX", "symbol": "NIFTY", "trading_symbol": "NIFTYFUT", "token": "9", "expiry": "28102026", "option_type": "", "strike": "0"},
    {"segment": "NFO", "instrument_type": "OPTIDX", "symbol": "NIFTY", "trading_symbol": "N-PE21800", "token": "2", "expiry": "08102026", "option_type": "PE", "strike": "21800"},
    {"segment": "NFO", "instrument_type": "OPTIDX", "symbol": "NIFTY", "trading_symbol": "N-PE21700", "token": "3", "expiry": "08102026", "option_type": "PE", "strike": "21700"},
]


def _candles(n: int, close: float, step: float = 0.0) -> list[Candle]:
    base = datetime(2024, 1, 1, 9, 15)
    out = []
    for i in range(n):
        ts = (base + timedelta(minutes=i)).isoformat()
        c = close + i * step
        out.append(Candle(ts=ts, open=c, high=c, low=c, close=c, volume=0, oi=None))
    return out


async def _ok(force: bool = False):
    return None


async def _rows(client):
    return ROWS


@pytest.fixture
def transforms_broker(monkeypatch):
    async def ensure_history(dg_client, **kwargs):
        if kwargs["kind"] == "future":
            return _candles(30, close=23000.0, step=5.0)
        if kwargs["strike"] == 21800:  # SELL leg, higher premium
            return _candles(30, close=200.0, step=1.0)
        return _candles(30, close=100.0, step=1.0)  # BUY leg

    monkeypatch.setattr(charts, "ensure_authenticated", _ok)
    monkeypatch.setattr(charts, "get_master_rows", _rows)
    monkeypatch.setattr(chart_history, "ensure_chart_history", ensure_history)


def _body(**overrides):
    body = {
        "symbol": "NIFTY",
        "expiry": "2026-10-08",
        "legs": [
            {"strike": 21800, "option_type": "PE", "action": "SELL"},
            {"strike": 21700, "option_type": "PE", "action": "BUY"},
        ],
    }
    body.update(overrides)
    return body


def test_transforms_shape_and_box_defaults(client, auth, transforms_broker):
    r = client.post("/api/v1/charts/transforms", json=_body(), headers=auth)
    assert r.status_code == 200
    data = r.json()

    assert data["interval_minutes"] == 5  # WS2 default
    assert data["reversal"] == 3
    series = data["series"]

    # future: reference 23000+29*5=23145 → box 0.1% = 23.145
    future = series["future"]
    assert future["reference_price"] == pytest.approx(23145.0)
    assert future["box_size"] == pytest.approx(23.145)
    assert future["box_pct"] == pytest.approx(0.1)  # no cap triggered on this data
    assert len(future["candles"]) == 6  # 30 × 1-min resampled to 5-min
    assert isinstance(future["renko"], list) and isinstance(future["pnf"], list)

    # legs: SELL 21800 → ref 229, BUY 21700 → ref 129 (option_box_pct default 1%)
    sell, buy = series["legs"]
    assert sell["strike"] == 21800 and sell["box_size"] == pytest.approx(2.29)
    assert buy["strike"] == 21700 and buy["box_size"] == pytest.approx(1.29)
    assert sell["box_pct"] == pytest.approx(1.0)
    assert buy["box_pct"] == pytest.approx(1.0)

    # combined: net credit -100 → ref 100, box 0.5% = 0.5
    combined = series["combined"]
    assert combined is not None
    assert combined["reference_price"] == pytest.approx(100.0)
    assert combined["box_size"] == pytest.approx(0.5)
    assert combined["box_pct"] == pytest.approx(0.5)

    # every series exposes the three views + box metadata
    for s in [future, sell, buy, combined]:
        for key in ("candles", "renko", "pnf", "box_size", "box_pct", "reference_price"):
            assert key in s


def test_transforms_box_and_reversal_override(client, auth, transforms_broker):
    r = client.post(
        "/api/v1/charts/transforms",
        json=_body(future_box_pct=0.25, option_box_pct=0.5, combined_box_pct=1.0, reversal=2),
        headers=auth,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["reversal"] == 2
    assert data["series"]["future"]["box_size"] == pytest.approx(23145.0 * 0.0025)
    assert data["series"]["future"]["box_pct"] == pytest.approx(0.25)
    sell, buy = data["series"]["legs"]
    assert sell["box_size"] == pytest.approx(229.0 * 0.005)
    assert sell["box_pct"] == pytest.approx(0.5)
    assert buy["box_size"] == pytest.approx(129.0 * 0.005)
    assert buy["box_pct"] == pytest.approx(0.5)
    assert data["series"]["combined"]["box_size"] == pytest.approx(100.0 * 0.01)
    assert data["series"]["combined"]["box_pct"] == pytest.approx(1.0)


def test_transforms_empty_candles_are_null_box(client, auth, transforms_broker, monkeypatch):
    async def ensure_history(dg_client, **kwargs):
        return []

    monkeypatch.setattr(chart_history, "ensure_chart_history", ensure_history)
    r = client.post("/api/v1/charts/transforms", json=_body(), headers=auth)
    assert r.status_code == 200
    future = r.json()["series"]["future"]
    assert future["candles"] == [] and future["renko"] == [] and future["pnf"] == []
    assert future["box_size"] is None and future["box_pct"] is None and future["reference_price"] is None


@pytest.mark.parametrize(
    "body, detail",
    [
        (_body(legs=[]), "At least one leg is required"),
        (_body(future_box_pct=0.0), "future_box_pct, option_box_pct and combined_box_pct must be positive"),
        (_body(option_box_pct=0.0), "future_box_pct, option_box_pct and combined_box_pct must be positive"),
        (_body(combined_box_pct=-1.0), "future_box_pct, option_box_pct and combined_box_pct must be positive"),
        (_body(reversal=0), "reversal must be >= 1"),
    ],
)
def test_transforms_400_on_bad_params(client, auth, transforms_broker, body, detail):
    r = client.post("/api/v1/charts/transforms", json=body, headers=auth)
    assert r.status_code == 400
    assert detail in r.json()["detail"]


def test_transforms_400_on_bad_interval(client, auth, transforms_broker):
    r = client.post("/api/v1/charts/transforms?interval=7", json=_body(), headers=auth)
    assert r.status_code == 400
    assert "interval must be one of" in r.json()["detail"]


def test_transforms_404_on_unknown_contract(client, auth, transforms_broker):
    r = client.post(
        "/api/v1/charts/transforms",
        json=_body(legs=[{"strike": 99999, "option_type": "CE", "action": "BUY"}]),
        headers=auth,
    )
    assert r.status_code == 404
