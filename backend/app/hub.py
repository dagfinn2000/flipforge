"""In-process fan-out for websocket clients."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("flipforge.hub")


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def join(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def leave(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    @property
    def size(self) -> int:
        return len(self._clients)

    async def broadcast(self, event: str, payload: Any) -> None:
        if not self._clients:
            return
        message = {"event": event, "data": payload}
        async with self._lock:
            targets = list(self._clients)
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001 - a dropped client must not break ingest
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


hub = Hub()
