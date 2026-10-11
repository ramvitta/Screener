import asyncio
import json
import logging
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security import authenticate_websocket
from app.workers.screener_loop import get_latest_signals

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/alerts")
async def ws_alerts(websocket: WebSocket):
    """Pushes new screener signals & position alerts. Polls the in-memory
    screener cache for now; swap for a pub/sub push once the ingest worker
    and position monitor emit events directly."""
    subprotocol = await authenticate_websocket(websocket)
    if subprotocol is None:
        return
    await websocket.accept(subprotocol=subprotocol or None)
    last_sent: Set[str] = set()
    try:
        while True:
            for signal in get_latest_signals():
                if signal["signal_id"] not in last_sent:
                    await websocket.send_text(json.dumps({"type": "screener_signal", "data": signal}))
                    last_sent.add(signal["signal_id"])
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        logger.info("ws/alerts client disconnected")


@router.websocket("/ws/quotes")
async def ws_quotes(websocket: WebSocket):
    """Live LTP/greeks stream for subscribed contracts. Relays from the
    Definedge WebSocket ingest worker once it is running — upstream is
    wss://trade.definedgesecurities.com/NorenWSTRTP/, logged in with
    {"t":"c","uid","actid","source":"TRTP","susertoken":<ws_session_key>}
    and subscribed to ticks with {"t":"t","k":"EXCH|TOKEN#EXCH|TOKEN..."}.
    Currently a stub that keeps the connection open for frontend
    integration testing."""
    subprotocol = await authenticate_websocket(websocket)
    if subprotocol is None:
        return
    await websocket.accept(subprotocol=subprotocol or None)
    try:
        while True:
            await websocket.send_text(json.dumps({"type": "heartbeat"}))
            await asyncio.sleep(10)
    except WebSocketDisconnect:
        logger.info("ws/quotes client disconnected")
