"""Policy and configuration endpoints. The tax rules are data, so they are
readable and the exemption list is editable at runtime."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db, policy, serial
from ..config import settings
from ..models import ExemptionIn

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def read_config():
    return {
        "version": settings.app_version,
        "source": settings.wiki_base,
        "user_agent": settings.user_agent,
        "tax_policy": policy.describe(),
        "poll_seconds": {
            "latest": settings.poll_latest_seconds,
            "five_minute": settings.poll_5m_seconds,
            "hourly": settings.poll_1h_seconds,
            "metrics": settings.metrics_interval_seconds,
        },
        "slots": {
            "members": settings.ge_slots_members,
            "free_to_play": settings.ge_slots_f2p,
        },
    }


@router.get("/exemptions")
async def list_exemptions():
    records = await db.fetch(
        """SELECT e.item_id, e.name, e.source, e.note, e.added_at, i.icon
             FROM tax_exemptions e LEFT JOIN items i ON i.id = e.item_id
            ORDER BY e.name"""
    )
    return {"results": serial.rows(records), "policy": policy.describe()}


@router.post("/exemptions")
async def add_exemption(body: ExemptionIn):
    name = await db.fetchval("SELECT name FROM items WHERE id = $1", body.item_id)
    if name is None:
        raise HTTPException(404, "unknown item")
    await db.execute(
        """INSERT INTO tax_exemptions (item_id, name, source, note)
           VALUES ($1, $2, 'manual', $3)
           ON CONFLICT (item_id) DO UPDATE SET note = EXCLUDED.note""",
        body.item_id, name, body.note,
    )
    await policy.reload()
    return {"ok": True, "policy": policy.describe()}


@router.delete("/exemptions/{item_id}")
async def remove_exemption(item_id: int):
    await db.execute("DELETE FROM tax_exemptions WHERE item_id = $1", item_id)
    await policy.reload()
    return {"ok": True, "policy": policy.describe()}
