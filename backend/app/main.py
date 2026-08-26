from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from . import db, ingest, serial
from .config import settings
from .hub import hub
from .routers import alerts, items, portfolio, scanner, watchlist, ws
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
    await db.connect()
    await db.migrate()
    if settings.contact.startswith("unset"):
        log.warning(
            "FF_CONTACT is not set. The OSRS Wiki asks API users to identify "
            "themselves; set it in .env before running this for any length of time."
        )
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
    description="Self-hosted real-time Old School RuneScape market intelligence.",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
    # Served under /api so the web container's proxy rule reaches them.
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# The API is meant to be usable from your own scripts and dashboards too.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (items, scanner, watchlist, alerts, portfolio, ws):
    app.include_router(module.router)


@app.get("/api/health")
async def health():
    items_count = await db.fetchval("SELECT count(*) FROM items")
    last_poll = await db.get_meta("last_latest_poll")
    last_metrics = await db.get_meta("last_metrics_run")
    now = int(time.time())
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - STARTED),
        "items": items_count,
        "ws_clients": hub.size,
        "seconds_since_price_poll": now - int(last_poll) if last_poll else None,
        "seconds_since_metrics": now - int(last_metrics) if last_metrics else None,
        "backfill_complete": await db.get_meta("backfill_done") == "1",
    }


@app.get("/api/config")
async def config():
    return {
        "version": settings.app_version,
        "tax": serial.tax_config(),
        "poll_seconds": settings.poll_latest_seconds,
        "source": settings.wiki_base,
    }
