from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Iterable, Optional

import asyncpg

from .config import settings

log = logging.getLogger("flipforge.db")

_pool: Optional[asyncpg.Pool] = None
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


async def connect(retries: int = 30, delay: float = 2.0) -> asyncpg.Pool:
    """Open the pool, waiting for Postgres to accept connections on cold start."""
    global _pool
    if _pool is not None:
        return _pool

    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            _pool = await asyncpg.create_pool(
                settings.database_url, min_size=2, max_size=12, command_timeout=60
            )
            log.info("connected to postgres")
            return _pool
        except Exception as exc:  # noqa: BLE001 - retry on anything until the DB is up
            last = exc
            log.warning("postgres not ready (%s/%s): %s", attempt, retries, exc)
            await asyncio.sleep(delay)
    raise RuntimeError(f"could not reach postgres: {last}")


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool is not initialised")
    return _pool


async def migrate() -> None:
    """Apply the schema and any migrations.

    Runs on its own connection with no command timeout: rewriting a column type
    across millions of candle rows, or converting an existing table into a
    hypertable, takes far longer than a normal query and must not be cut off
    half way through.
    """
    sql = SCHEMA_PATH.read_text()
    conn = await asyncpg.connect(settings.database_url, command_timeout=None)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()
    log.info("schema applied")


async def run_long(query: str, *args: Any, fetch_rows: bool = False):
    """Run one analytical statement on its own connection with no time limit.

    The pool caps commands at 60 seconds, which is right for request handling
    and wrong for month-wide aggregates: a statement that scans a million
    snapshots is not a stuck query, and killing it means the work never
    completes at all.
    """
    conn = await asyncpg.connect(settings.database_url, command_timeout=None)
    try:
        return await (conn.fetch(query, *args) if fetch_rows else conn.execute(query, *args))
    finally:
        await conn.close()


async def fetch(query: str, *args: Any) -> list[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetch(query, *args)


async def fetchrow(query: str, *args: Any) -> Optional[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetchrow(query, *args)


async def fetchval(query: str, *args: Any) -> Any:
    async with pool().acquire() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args: Any) -> str:
    async with pool().acquire() as conn:
        return await conn.execute(query, *args)


async def executemany(query: str, rows: Iterable[Iterable[Any]]) -> None:
    batch = list(rows)
    if not batch:
        return
    async with pool().acquire() as conn:
        await conn.executemany(query, batch)


async def get_meta(key: str) -> Optional[str]:
    return await fetchval("SELECT value FROM meta WHERE key = $1", key)


async def set_meta(key: str, value: str) -> None:
    await execute(
        """INSERT INTO meta (key, value, updated_at) VALUES ($1, $2, now())
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
        key,
        value,
    )
