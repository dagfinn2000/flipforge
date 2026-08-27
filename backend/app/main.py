from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from . import db, ingest, policy
from .config import settings
from .hub import hub
from .routers import alerts, allocator, items, portfolio, scanner, validation, watchlist, ws
from .routers import config as config_router
from .wiki import client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("flipforge")

STARTED = time.time()
_tasks: list[asyncio.Task] = []


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail loudly and immediately rather than emitting a wall of rejected
    # requests that looks like a network fault.
    try:
        settings.require_contact()
    except RuntimeError as exc:
        log.error("%s", exc)
        sys.exit(1)

    await db.connect()
    await db.migrate()
    try:
        await client.probe()
    except Exception as exc:  # noqa: BLE001
        log.error("upstream check failed: %s", exc)
        sys.exit(1)

    await ingest.start_background(_tasks)
    log.info("flipforge ready")
    try:
        yield
    finally:
        for task in _tasks:
            task.cancel()
        await asyncio.gather(*_tasks, return_exceptions=True)
        await client.aclose()
        await db.close()


app = FastAPI(
    title="FlipForge",
    version=settings.app_version,
    description=(
        "Self-hosted real-time Old School RuneScape market intelligence. "
        "Every margin reported by this API is net of Grand Exchange tax."
    ),
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
    # Served under /api so the web container's proxy rule reaches them.
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# The API is meant to be usable from your own scripts and dashboards too.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

for module in (items, scanner, watchlist, alerts, portfolio, allocator, validation, config_router, ws):
    app.include_router(module.router)


@app.get("/api/health", tags=["system"])
async def health():
    now = int(time.time())
    last_poll = await db.get_meta("last_latest_poll")
    last_metrics = await db.get_meta("last_metrics_run")
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - STARTED),
        "items": await db.fetchval("SELECT count(*) FROM items"),
        "ws_clients": hub.size,
        "seconds_since_price_poll": now - int(last_poll) if last_poll else None,
        "seconds_since_metrics": now - int(last_metrics) if last_metrics else None,
        "backfill_complete": await db.get_meta("backfill_done") == "1",
        "tax_policy": policy.describe(),
    }
