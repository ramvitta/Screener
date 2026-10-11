"""Contract tests: the broker client against recorded/anonymised response
shapes. No network — every call is intercepted by respx."""

import csv
import io
import zipfile
from datetime import datetime
from hashlib import sha256

import httpx
import pytest
import respx

from app.config import settings
from app.integrations import definedge_client as dc
from app.integrations.definedge_client import DefinedgeAuthError, DefinedgeClient, DefinedgeSession


def authed_client() -> DefinedgeClient:
    c = DefinedgeClient()
    c._session = DefinedgeSession("U1", "A1", "api-key-1", "ws-key-1", datetime(2099, 1, 1))
    return c


@respx.mock
async def test_login_step2_sends_documented_hash():
    c = DefinedgeClient()
    route = respx.post(url__regex=r".*/token").mock(
        return_value=httpx.Response(200, json={"uid": "U1", "actid": "A1", "api_session_key": "k1", "susertoken": "k2"})
    )
    s = await c.login_step2("OTPTOK", "123456")
    sent = route.calls[0].request.content.decode()
    expected_ac = sha256(f"OTPTOK123456{settings.definedge_api_secret}".encode()).hexdigest()
    assert expected_ac in sent and settings.definedge_api_secret not in sent
    assert (s.api_session_key, s.ws_session_key) == ("k1", "k2")


@respx.mock
async def test_http_200_with_status_error_is_treated_as_expired_session():
    c = authed_client()
    invalidated = []
    c.on_invalidated = lambda: invalidated.append(True)
    respx.get(url__regex=r".*/quotes/NSE/26000").mock(return_value=httpx.Response(200, json={"status": "ERROR", "message": "Session Expired"}))
    with pytest.raises(DefinedgeAuthError):
        await c.get_quote("NSE", "26000")
    assert c._session is None and invalidated


@respx.mock
async def test_http_401_is_treated_as_expired_session():
    c = authed_client()
    respx.get(url__regex=r".*/limits").mock(return_value=httpx.Response(401))
    with pytest.raises(DefinedgeAuthError):
        await c.get_limits()
    assert c._session is None


@respx.mock
async def test_unauthenticated_call_refused_before_any_request():
    c = DefinedgeClient()
    with pytest.raises(DefinedgeAuthError):
        await c.get_limits()


@respx.mock
async def test_history_csv_parsing_with_and_without_oi():
    c = authed_client()
    csv_body = "011020260915,100,110,95,105,1000,5000\n011020260916,105,106,104,105.5,10\nbad,row\n"
    respx.get(url__regex=r".*/sds/history/.*").mock(return_value=httpx.Response(200, text=csv_body))
    candles = await c.get_historical_candles("NFO", "123", "minute", datetime(2026, 10, 1, 9, 0), datetime(2026, 10, 1, 10, 0))
    assert [x["close"] for x in candles] == [105.0, 105.5]
    assert candles[0]["oi"] == 5000 and candles[1]["oi"] is None


async def test_invalid_timeframe_rejected():
    with pytest.raises(ValueError):
        await authed_client().get_historical_candles("NFO", "1", "week", datetime.now(), datetime.now())


def _master_zip(rows, name="allmaster.csv") -> bytes:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(name, buf.getvalue())
    return out.getvalue()


GOOD = ["NFO", "123", "NIFTY", "NIFTY08OCT2621800PE", "OPTIDX", "08102026", "5", "75", "PE", "2180000", "2", "1", "", "1"]


@respx.mock
async def test_instrument_master_skips_malformed_rows_and_zero_divisor():
    zero_div = GOOD[:10] + ["2", "0"] + GOOD[12:]
    short = ["NFO", "1"]
    respx.get(dc.MASTER_URL).mock(return_value=httpx.Response(200, content=_master_zip([GOOD, short, zero_div])))
    rows = await DefinedgeClient().get_instrument_master()
    assert len(rows) == 1 and rows[0]["trading_symbol"] == "NIFTY08OCT2621800PE" and rows[0]["strike"] == "21800"


@respx.mock
async def test_instrument_master_size_limit(monkeypatch):
    monkeypatch.setattr(dc, "MAX_MASTER_ZIP_BYTES", 10)
    respx.get(dc.MASTER_URL).mock(return_value=httpx.Response(200, content=_master_zip([GOOD])))
    with pytest.raises(ValueError, match="size limit"):
        await DefinedgeClient().get_instrument_master()


@respx.mock
async def test_instrument_master_unexpected_archive_layout_fails_safely():
    respx.get(dc.MASTER_URL).mock(return_value=httpx.Response(200, content=_master_zip([GOOD], name="../../etc/evil.csv")))
    with pytest.raises(KeyError):  # we only ever read the one expected member
        await DefinedgeClient().get_instrument_master()


def test_session_repr_hides_keys():
    assert "api-key-1" not in repr(authed_client()._session)
