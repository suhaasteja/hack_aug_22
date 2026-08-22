"""Web module: live dashboard over a WebSocket fan-out of the event bus.

Read-only — it subscribes to every event type and forwards to connected
browsers. New event types show up in later stages without changes here;
the frontend decides how to render each one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from app.bus import EventBus
from app.events import Event

log = logging.getLogger("web")

STATIC_DIR = Path(__file__).parent / "static"
HISTORY_MAX = 500


class Hub:
    """Fans bus events out to browsers and replays history to late joiners."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._history: deque[dict[str, Any]] = deque(maxlen=HISTORY_MAX)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        for event in list(self._history):
            await ws.send_json(event)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, event: Event) -> None:
        payload = event.to_dict()
        self._history.append(payload)
        dead = []
        for ws in self._clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


def build_app(hub: Hub) -> FastAPI:
    app = FastAPI(title="VoC Factory")

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await hub.connect(ws)
        try:
            while True:
                await ws.receive_text()  # clients don't send; this detects close
        except WebSocketDisconnect:
            hub.disconnect(ws)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


async def run(bus: EventBus, config: dict[str, Any], module_config: dict[str, Any]) -> None:
    hub = Hub()
    host = module_config.get("host", "127.0.0.1")
    port = int(module_config.get("port", 7000))

    server = uvicorn.Server(
        uvicorn.Config(build_app(hub), host=host, port=port, log_level="warning")
    )
    serving = asyncio.create_task(server.serve())
    log.info("dashboard on http://%s:%d", host, port)

    try:
        async for event in bus.stream():
            await hub.broadcast(event)
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await serving
