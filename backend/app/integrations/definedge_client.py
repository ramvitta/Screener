"""
Definedge Securities Integrate API client.

Endpoint paths, auth flow, and payload shapes verified against the official
Python SDK, `pyintegrate` (https://github.com/Definedge-Securities/pyintegrate,
`integrate/connect.py` / `data.py` / `orders.py` on `main`) — the hosted docs
at https://www.definedgesecurities.com/api-documentation/ don't expose raw
endpoint paths, so the SDK source is the authoritative reference.
"""

from csv import reader
from datetime import datetime, timedelta
from hashlib import sha256
from io import BytesIO
from typing import Callable, List, Optional, TypedDict
from zipfile import ZipFile

import httpx
import pyotp

from app.config import settings


class Candle(TypedDict):
    ts: str  # ISO 8601, Asia/Kolkata
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: Optional[int]

LOGIN_URL = "https://signin.definedgesecurities.com/auth/realms/debroking/dsbpkc"
BASE_URL = "https://integrate.definedgesecurities.com/dart/v1"
HISTORY_URL = "https://data.definedgesecurities.com/sds/history"
MASTER_URL = "https://app.definedgesecurities.com/public/allmaster.zip"

VALID_TIMEFRAMES = {"minute", "day", "tick"}

MAX_MASTER_ZIP_BYTES = 50 * 1024 * 1024
MAX_MASTER_CSV_BYTES = 500 * 1024 * 1024


class DefinedgeAuthError(RuntimeError):
    pass


class DefinedgeSession:
    """Holds the four values returned by the login flow.

    `api_session_key` authenticates REST calls (`Authorization` header);
    `ws_session_key` (the `susertoken` field in the login response)
    authenticates the WebSocket login frame instead — they are not
    interchangeable.
    """

    def __init__(self, uid: str, actid: str, api_session_key: str, ws_session_key: str, expires_at: datetime):
        self.uid = uid
        self.actid = actid
        self.api_session_key = api_session_key
        self.ws_session_key = ws_session_key
        self.expires_at = expires_at

    def __repr__(self) -> str:  # never let session keys land in a log/traceback
        return f"DefinedgeSession(expires_at={self.expires_at.isoformat()})"

    @property
    def is_expired(self) -> bool:
        return datetime.now() >= self.expires_at

    def expires_within(self, buffer: timedelta) -> bool:
        """True once we're inside `buffer` of expiry (or already past it) —
        used by ensure_authenticated() to refresh proactively instead of
        waiting for an outright 401."""
        return datetime.now() >= self.expires_at - buffer


class DefinedgeClient:
    """Thin async wrapper around the Definedge Integrate REST API.

    Two-step login: (1) GET login/{api_token} with an `api_secret` header to
    get an `otp_token`, (2) the user supplies an OTP/TOTP; compute
    `ac = sha256(otp_token + otp + api_secret)` and POST it with the OTP to
    `token`, which returns the session keys.
    """

    def __init__(self, session: Optional[DefinedgeSession] = None):
        self._session = session
        self._login_http = httpx.AsyncClient(base_url=LOGIN_URL, timeout=10.0)
        self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=10.0)
        self._history_http = httpx.AsyncClient(base_url=HISTORY_URL, timeout=30.0)
        # Set by auth.py so an invalidated session also gets deactivated in
        # SQLite — otherwise ensure_authenticated() would keep restoring the
        # same broker-side-revoked session from the DB forever (see
        # _invalidate()).
        self.on_invalidated: Optional[Callable[[], None]] = None

    def _invalidate(self) -> None:
        self._session = None
        if self.on_invalidated:
            self.on_invalidated()

    async def login_step1(self) -> str:
        resp = await self._login_http.get(
            f"/login/{settings.definedge_api_token}",
            headers={"api_secret": settings.definedge_api_secret},
        )
        resp.raise_for_status()
        return resp.json()["otp_token"]

    async def login_step2(self, otp_token: str, otp_code: str) -> DefinedgeSession:
        ac = sha256(f"{otp_token}{otp_code}{settings.definedge_api_secret}".encode("utf-8")).hexdigest()
        resp = await self._login_http.post(
            "/token",
            json={"otp_token": otp_token, "otp": otp_code, "ac": ac},
        )
        resp.raise_for_status()
        data = resp.json()
        session = DefinedgeSession(
            uid=data["uid"],
            actid=data["actid"],
            api_session_key=data["api_session_key"],
            ws_session_key=data["susertoken"],
            expires_at=datetime.now() + timedelta(hours=12),
        )
        self._session = session
        return session

    async def login(self) -> DefinedgeSession:
        """Full login using the external TOTP secret from settings, bypassing
        the manual OTP prompt/endpoint. Requires `DEFINEDGE_TOTP_SECRET` to be
        set (enabled at https://myaccount.definedgesecurities.com/security)."""
        if not settings.definedge_totp_secret:
            raise DefinedgeAuthError(
                "DEFINEDGE_TOTP_SECRET is not set — cannot auto-login. "
                "Use login_step1/login_step2 with a manually supplied OTP instead."
            )
        otp_token = await self.login_step1()
        otp_code = pyotp.TOTP(settings.definedge_totp_secret).now()
        return await self.login_step2(otp_token, otp_code)

    def _auth_headers(self) -> dict:
        if not self._session:
            raise DefinedgeAuthError("Not authenticated — call login_step1/login_step2 first.")
        return {"Authorization": self._session.api_session_key}

    async def _authed_get(self, client: httpx.AsyncClient, path: str) -> httpx.Response:
        """GET with the current session's auth header. Definedge signals an
        expired/revoked session two different ways depending on the
        endpoint: a real HTTP 401, or (observed on /quotes) an HTTP 200 with
        a JSON body `{"status": "ERROR", "message": "Session Expired..."}` —
        so both are treated the same: invalidate the session (clearing it
        in-memory *and* deactivating it in SQLite via on_invalidated, so
        ensure_authenticated() doesn't just keep restoring the same
        broker-side-revoked row) and raise DefinedgeAuthError so the
        caller's ensure_authenticated() re-logs-in on the next attempt —
        this stays a one-shot signal rather than an automatic
        retry-in-place, so auth failures remain visible in logs."""
        resp = await client.get(path, headers=self._auth_headers())
        if resp.status_code == 401:
            self._invalidate()
            raise DefinedgeAuthError("Definedge session expired or was revoked (401) — re-login required.")
        resp.raise_for_status()
        try:
            body = resp.json()
        except ValueError:
            return resp  # not JSON (e.g. the CSV historical-candles endpoint) — nothing to check
        if isinstance(body, dict) and body.get("status") == "ERROR":
            self._invalidate()
            raise DefinedgeAuthError(f"Definedge session invalid: {body.get('message', 'unknown error')}")
        return resp

    async def get_instrument_master(self) -> List[dict]:
        """Downloads and parses the public instrument master file.

        Not an authenticated REST call — it's a static zip download shared
        across all Definedge Integrate users. The download is untrusted
        input: size-capped, and rows are validated before use.
        """
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(MASTER_URL)
        resp.raise_for_status()
        if len(resp.content) > MAX_MASTER_ZIP_BYTES:
            raise ValueError("Instrument master download exceeds size limit")
        with ZipFile(BytesIO(resp.content)) as z:
            info = z.getinfo("allmaster.csv")
            if info.file_size > MAX_MASTER_CSV_BYTES:
                raise ValueError("Instrument master CSV exceeds size limit")
            with z.open("allmaster.csv") as f:
                lines = f.read().decode("utf-8").splitlines()
        instruments = []
        for row in reader(lines):
            if len(row) < 14:
                continue  # malformed row — skip rather than index past the end
            try:
                divisor = int(row[11]) * 10 ** int(row[10])
                if divisor == 0:
                    continue  # unusable price multiplier — skip the row
                strike = str(int(int(row[9]) / divisor))
            except ValueError:
                continue
            instruments.append(
                {
                    "segment": row[0],
                    "token": row[1],
                    "symbol": row[2],
                    "trading_symbol": row[3],
                    "instrument_type": row[4],
                    "expiry": row[5],
                    "tick_size": row[6],
                    "lot_size": row[7],
                    "option_type": row[8],
                    "strike": strike,
                    "isin": row[12],
                    "price_mult": row[13],
                }
            )
        return instruments

    async def get_quote(self, exchange: str, token: str) -> dict:
        resp = await self._authed_get(self._http, f"/quotes/{exchange}/{token}")
        return resp.json()

    async def get_historical_candles(
        self, exchange: str, token: str, timeframe: str, start: datetime, end: datetime
    ) -> List[Candle]:
        """Definedge's historical endpoint returns CSV, not JSON — each line is
        `DDMMYYYYHHMM,open,high,low,close,volume[,oi]` (oi present for F&O,
        absent for cash/index instruments)."""
        if timeframe not in VALID_TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {VALID_TIMEFRAMES}, got {timeframe!r}")
        resp = await self._authed_get(
            self._history_http,
            f"/{exchange}/{token}/{timeframe}/{start.strftime('%d%m%Y%H%M')}/{end.strftime('%d%m%Y%H%M')}",
        )
        candles: List[Candle] = []
        for row in reader(resp.text.strip().splitlines()):
            if len(row) < 6:
                continue
            ts = datetime.strptime(row[0], "%d%m%Y%H%M")
            candles.append(
                Candle(
                    ts=ts.isoformat(),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=int(float(row[5])),
                    oi=int(float(row[6])) if len(row) > 6 else None,
                )
            )
        return candles

    async def get_positions(self) -> List[dict]:
        resp = await self._authed_get(self._http, "/positions")
        return resp.json().get("positions", [])

    async def get_holdings(self) -> List[dict]:
        resp = await self._authed_get(self._http, "/holdings")
        return resp.json().get("data", [])

    async def get_limits(self) -> dict:
        resp = await self._authed_get(self._http, "/limits")
        return resp.json()

    async def close(self) -> None:
        await self._login_http.aclose()
        await self._http.aclose()
        await self._history_http.aclose()
