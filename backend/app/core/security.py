"""API access control: static bearer token on every route and WebSocket,
plus a small in-process rate limiter for the sensitive endpoints."""

import hmac
import time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, List, Optional

from fastapi import HTTPException, Request, WebSocket

from app.config import settings

WS_SUBPROTOCOL_PREFIX = "bearer."


def _token_matches(candidate: Optional[str]) -> bool:
    expected = settings.api_auth_token
    if not expected or not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


async def require_auth(request: Request) -> None:
    """FastAPI dependency. Fails closed: an unconfigured token rejects
    everything instead of letting requests through."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not _token_matches(token.strip()):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _origin_allowed(origin: Optional[str]) -> bool:
    # Browsers always send Origin on WebSocket handshakes; non-browser clients
    # (scripts) may omit it and still have to present the token.
    return origin is None or origin in settings.cors_origins


async def authenticate_websocket(websocket: WebSocket) -> Optional[str]:
    """Validates Origin + bearer token before accepting. Browsers can't set
    an Authorization header on a WebSocket, so the token is also accepted as
    the `bearer.<token>` subprotocol (never in the URL, which would be
    logged). Returns the subprotocol to echo back (or "" when a header was
    used), or None after closing the socket."""
    if not _origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=1008)
        return None

    header = websocket.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and _token_matches(token.strip()):
        return ""

    for proto in websocket.scope.get("subprotocols", []):
        if proto.startswith(WS_SUBPROTOCOL_PREFIX) and _token_matches(proto[len(WS_SUBPROTOCOL_PREFIX):]):
            return proto

    await websocket.close(code=1008)
    return None


class RateLimiter:
    """Sliding-window limiter keyed by client host. In-process only, which is
    fine for the single-user, single-process deployment this app targets."""

    def __init__(self, max_calls: int, per_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.max_calls = max_calls
        self.per_seconds = per_seconds
        self._clock = clock
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        now = self._clock()
        hits = self._hits[key]
        while hits and now - hits[0] > self.per_seconds:
            hits.popleft()
        if len(hits) >= self.max_calls:
            return False
        hits.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


_limiters: List[RateLimiter] = []


def rate_limit(max_calls: int, per_seconds: float):
    """Dependency factory: `Depends(rate_limit(5, 60))`."""
    limiter = RateLimiter(max_calls, per_seconds)
    _limiters.append(limiter)

    async def dependency(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        if not limiter.check(client):
            raise HTTPException(status_code=429, detail="Too many requests", headers={"Retry-After": str(int(per_seconds))})

    return dependency


def reset_rate_limiters() -> None:
    for limiter in _limiters:
        limiter.reset()
