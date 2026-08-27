"""Data retention.

The database is otherwise unbounded: roughly 550,000 candle rows and 380,000
score rows a day, which is on the order of 50GB a year. Everything here is
driven by what actually reads the data — the windows in settings were chosen by
auditing each query's time range, not picked round.

Two mechanisms, used where each fits:

  drop_chunks  is near-instant and reclaims space immediately, but works on
               whole time chunks, so it can only express "older than X" for an
               entire hypertable.
  DELETE       is slower and leaves space for autovacuum to reclaim, but can
               filter on a column. Needed because candles of every resolution
               share one table and deserve very different lifetimes.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from . import db
from .config import settings

log = logging.getLogger("flipforge.maintenance")

# Tables to report on, in the order a human would want to read them.
SIZED_TABLES = (
    "candles", "score_outcomes", "score_snapshots", "metrics",
    "latest", "items", "alert_events", "trades",
)


async def _delete_candles(timestep: str, days: int) -> int:
    """Drop one candle resolution past its useful life."""
    if days <= 0:
        return 0
    result = await db.execute(
        """DELETE FROM candles
            WHERE timestep = $1 AND ts < now() - ($2 || ' days')::interval""",
        timestep, str(days),
    )
    return int(result.split()[-1]) if result.startswith("DELETE") else 0


async def _drop_chunks(table: str, days: int) -> int:
    """Drop whole chunks older than `days`. Returns the number dropped."""
    if days <= 0:
        return 0
    rows = await db.fetch(
        "SELECT drop_chunks($1, older_than => ($2 || ' days')::interval) AS chunk",
        table, str(days),
    )
    return len(rows)


async def _delete_rows(table: str, column: str, days: int) -> int:
    if days <= 0:
        return 0
    result = await db.execute(
        f"DELETE FROM {table} WHERE {column} < now() - ($1 || ' days')::interval",
        str(days),
    )
    return int(result.split()[-1]) if result.startswith("DELETE") else 0


async def database_size() -> dict:
    """Current on-disk footprint, per table, for the storage endpoint."""
    total = await db.fetchval("SELECT pg_database_size(current_database())")
    tables = []
    for name in SIZED_TABLES:
        exists = await db.fetchval("SELECT to_regclass($1)", f"public.{name}")
        if not exists:
            continue
        # hypertable_size covers a hypertable's chunks; plain tables need the
        # ordinary relation size.
        size = await db.fetchval(
            """SELECT CASE
                    WHEN EXISTS (SELECT 1 FROM timescaledb_information.hypertables
                                  WHERE hypertable_name = $1)
                    THEN hypertable_size($2::regclass)
                    ELSE pg_total_relation_size($2::regclass)
               END""",
            name, f"public.{name}",
        )
        rows = await db.fetchval(f"SELECT count(*) FROM {name}")
        tables.append({"table": name, "bytes": int(size or 0), "rows": int(rows or 0)})
    tables.sort(key=lambda t: t["bytes"], reverse=True)
    return {"total_bytes": int(total or 0), "tables": tables}


async def run(reason: str = "scheduled") -> dict:
    """Apply every retention rule once. Safe to run at any time."""
    started = time.perf_counter()
    before = await db.fetchval("SELECT pg_database_size(current_database())")

    report = {
        "candles_5m_deleted": await _delete_candles("5m", settings.retain_5m_days),
        "candles_1h_deleted": await _delete_candles("1h", settings.retain_1h_days),
        "candle_chunks_dropped": await _drop_chunks("candles", settings.retain_candles_days),
        "snapshot_chunks_dropped": await _drop_chunks(
            "score_snapshots", settings.retain_snapshots_days
        ),
        "outcome_chunks_dropped": await _drop_chunks(
            "score_outcomes", settings.retain_outcomes_days
        ),
        "alert_events_deleted": await _delete_rows(
            "alert_events", "created_at", settings.retain_alert_events_days
        ),
    }

    after = await db.fetchval("SELECT pg_database_size(current_database())")
    report["bytes_before"] = int(before or 0)
    report["bytes_after"] = int(after or 0)
    # DELETE hands space back to autovacuum rather than the filesystem, so this
    # can read as zero or negative even when a lot of rows went away.
    report["bytes_freed"] = int((before or 0) - (after or 0))
    report["seconds"] = round(time.perf_counter() - started, 2)
    report["reason"] = reason

    await db.set_meta("last_maintenance", str(int(time.time())))
    log.info(
        "maintenance (%s): 5m -%s, 1h -%s, chunks -%s, snapshots -%s, outcomes -%s in %.1fs",
        reason, report["candles_5m_deleted"], report["candles_1h_deleted"],
        report["candle_chunks_dropped"], report["snapshot_chunks_dropped"],
        report["outcome_chunks_dropped"], report["seconds"],
    )
    return report


def policy() -> dict:
    """The retention rules in force, for display."""
    return {
        "candles_5m_days": settings.retain_5m_days,
        "candles_1h_days": settings.retain_1h_days,
        "candles_other_days": settings.retain_candles_days,
        "score_snapshots_days": settings.retain_snapshots_days,
        "score_outcomes_days": settings.retain_outcomes_days,
        "alert_events_days": settings.retain_alert_events_days,
        "interval_seconds": settings.maintenance_interval_seconds,
        "note": (
            "Candles are a cache of an upstream API and can always be refetched; "
            "opening an item page repopulates its history on demand. Trades, "
            "watchlist, alert rules and tax exemptions are never touched."
        ),
    }
