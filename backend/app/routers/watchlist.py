from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import db, serial
from ..models import WatchIn

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


@router.get("")
async def list_watchlist():
    records = await db.fetch(
        """SELECT i.id, i.name, i.icon, i.members, i.buy_limit, w.note, w.created_at,
                  m.high, m.low, m.margin, m.roi, m.vol_24h, m.flip_score,
                  m.price_change_1h, m.price_change_24h, m.rsi_14, m.data_age_seconds
             FROM watchlist w
             JOIN items i ON i.id = w.item_id
             LEFT JOIN metrics m ON m.item_id = w.item_id
            ORDER BY m.flip_score DESC NULLS LAST, i.name"""
    )
    return {"results": serial.rows(records)}


@router.post("")
async def add(body: WatchIn):
    if not await db.fetchval("SELECT 1 FROM items WHERE id = $1", body.item_id):
        raise HTTPException(404, "unknown item")
    await db.execute(
        """INSERT INTO watchlist (item_id, note) VALUES ($1, $2)
           ON CONFLICT (item_id) DO UPDATE SET note = EXCLUDED.note""",
        body.item_id, body.note,
    )
    return {"ok": True, "item_id": body.item_id}


@router.delete("/{item_id}")
async def remove(item_id: int):
    await db.execute("DELETE FROM watchlist WHERE item_id = $1", item_id)
    return {"ok": True}
