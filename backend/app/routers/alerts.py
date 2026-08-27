from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import db, serial
from ..ingest import ALERT_METRICS
from ..models import AlertIn

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("")
async def list_alerts():
    records = await db.fetch(
        """SELECT a.*, i.name, i.icon,
                  m.high, m.low, m.margin, m.roi, m.vol_1h, m.zscore_24h, m.flip_score
             FROM alerts a JOIN items i ON i.id = a.item_id
             LEFT JOIN metrics m ON m.item_id = a.item_id
            ORDER BY a.created_at DESC"""
    )
    out = serial.rows(records)
    for r in out:
        current = r.get(r["metric"])
        r["current_value"] = current
        if current is not None:
            r["distance"] = current - r["threshold"]
    return {"results": out, "metrics": ALERT_METRICS}


@router.post("")
async def create(body: AlertIn):
    if not await db.fetchval("SELECT 1 FROM items WHERE id = $1", body.item_id):
        raise HTTPException(404, "unknown item")
    alert_id = await db.fetchval(
        """INSERT INTO alerts (item_id, metric, op, threshold, hysteresis, note, cooldown_s)
           VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id""",
        body.item_id, body.metric, body.op, body.threshold,
        body.hysteresis, body.note, body.cooldown_s,
    )
    return {"ok": True, "id": alert_id}


@router.delete("/{alert_id}")
async def remove(alert_id: int):
    await db.execute("DELETE FROM alerts WHERE id = $1", alert_id)
    return {"ok": True}


@router.post("/{alert_id}/toggle")
async def toggle(alert_id: int):
    active = await db.fetchval(
        "UPDATE alerts SET active = NOT active WHERE id = $1 RETURNING active", alert_id
    )
    if active is None:
        raise HTTPException(404, "unknown alert")
    return {"ok": True, "active": active}


@router.get("/events")
async def events(limit: int = Query(50, ge=1, le=200), unseen_only: bool = False):
    where = "WHERE NOT e.seen" if unseen_only else ""
    records = await db.fetch(
        f"""SELECT e.*, i.name, i.icon FROM alert_events e
            JOIN items i ON i.id = e.item_id {where}
            ORDER BY e.created_at DESC LIMIT $1""",
        limit,
    )
    return {"results": serial.rows(records)}


@router.post("/events/seen")
async def mark_seen():
    await db.execute("UPDATE alert_events SET seen = TRUE WHERE NOT seen")
    return {"ok": True}
