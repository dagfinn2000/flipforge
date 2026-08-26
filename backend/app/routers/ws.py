from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..hub import hub

router = APIRouter()


@router.websocket("/ws")
async def stream(ws: WebSocket) -> None:
    """Push ingest ticks and alert firings to the browser as they happen."""
    await hub.join(ws)
    try:
        await ws.send_json({"event": "hello", "data": {"clients": hub.size}})
        while True:
            # Client messages are only used as a liveness ping.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(ws.receive_text(), timeout=30)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - any transport error just ends the session
        pass
    finally:
        await hub.leave(ws)
