from __future__ import annotations

from fastapi import APIRouter

from .. import allocator as solver
from .. import db, serial
from ..config import settings
from ..models import AllocatorIn, AllocatorPrefIn
from ..money import BUY_LIMIT_WINDOW_SECONDS
from ..scoring import fillable_quantity

router = APIRouter(prefix="/api/allocator", tags=["allocator"])

CANDIDATE_SQL = """
SELECT i.id, i.name, i.icon, i.members, i.buy_limit,
       m.low, m.margin, m.flip_score, m.vol_24h, m.est_fill_hours
  FROM metrics m
  JOIN items i ON i.id = m.item_id
 WHERE m.margin > 0
   AND m.low IS NOT NULL AND m.low > 0
   AND COALESCE(m.vol_24h, 0) >= $1
   AND COALESCE(m.flip_score, 0) >= $2
   AND COALESCE(m.data_age_seconds, 999999) <= $3
   AND m.low <= $4
   AND ($5::boolean IS NULL OR i.members = $5)
   AND NOT (i.id = ANY($6::int[]))
 ORDER BY m.flip_score DESC NULLS LAST
 LIMIT 400
"""


@router.post("/solve")
async def solve(body: AllocatorIn):
    """Spread a bankroll across the Grand Exchange slots.

    Answers the question a ranked list cannot: given this much gold and this
    many slots, what should actually be bought right now.
    """
    excluded = set(body.excluded)
    stored = await db.fetch("SELECT item_id, mode FROM allocator_prefs")
    for pref in stored:
        (excluded.add(pref["item_id"]) if pref["mode"] == "exclude" else None)
    pinned = set(body.pinned) | {p["item_id"] for p in stored if p["mode"] == "pin"}
    excluded -= pinned

    records = await db.fetch(
        CANDIDATE_SQL,
        body.min_volume, body.min_score, body.max_quote_age,
        body.bankroll, body.members, list(excluded),
    )

    # Buy limits are a rolling 4 hour window, so what matters is how much of
    # each limit is still available -- not the published number. Advising a
    # purchase the game will refuse is worse than advising nothing.
    spent = {
        r["item_id"]: int(r["used"])
        for r in await db.fetch(
            """SELECT item_id, SUM(quantity) AS used FROM trades
                WHERE side = 'buy'
                  AND executed_at > now() - make_interval(secs => $1)
                GROUP BY item_id""",
            BUY_LIMIT_WINDOW_SECONDS,
        )
    }

    candidates, exhausted = [], []
    for r in records:
        volume = int(r["vol_24h"] or 0)
        used = spent.get(r["id"], 0)
        limit = r["buy_limit"]
        remaining = max(limit - used, 0) if limit else None
        if limit and remaining == 0:
            exhausted.append({"item_id": r["id"], "name": r["name"], "used": used, "limit": limit})
            continue
        quantity = fillable_quantity(remaining if limit else None, volume)
        if quantity <= 0:
            continue
        candidates.append(
            solver.Candidate(
                item_id=r["id"], name=r["name"], price=int(r["low"]), margin=int(r["margin"]),
                max_quantity=quantity, score=float(r["flip_score"] or 0),
                volume_24h=volume, buy_limit=limit, limit_used=used,
                est_fill_hours=r["est_fill_hours"], icon_url=serial.icon_url(r["icon"]),
                members=r["members"],
            )
        )

    plan = solver.solve(
        candidates,
        bankroll=body.bankroll,
        slots=body.slots,
        max_share=body.max_share,
        pinned_ids=list(pinned),
    )
    result = plan.as_dict()
    result["candidates_considered"] = len(candidates)
    result["pinned"] = sorted(pinned)
    result["excluded"] = sorted(excluded)
    result["slot_reference"] = {
        "members": settings.ge_slots_members,
        "free_to_play": settings.ge_slots_f2p,
    }
    result["limit_exhausted"] = exhausted
    if exhausted:
        plan.notes.append(
            f"{len(exhausted)} item(s) skipped: their 4 hour buy limit is already "
            "spent according to your trade log"
        )
        result["notes"] = plan.notes
    return result


@router.get("/prefs")
async def list_prefs():
    records = await db.fetch(
        """SELECT p.item_id, p.mode, i.name, i.icon
             FROM allocator_prefs p JOIN items i ON i.id = p.item_id
            ORDER BY p.created_at DESC"""
    )
    return {"results": serial.rows(records)}


@router.post("/prefs")
async def set_pref(body: AllocatorPrefIn):
    await db.execute(
        """INSERT INTO allocator_prefs (item_id, mode) VALUES ($1, $2)
           ON CONFLICT (item_id) DO UPDATE SET mode = EXCLUDED.mode""",
        body.item_id, body.mode,
    )
    return {"ok": True}


@router.delete("/prefs/{item_id}")
async def clear_pref(item_id: int):
    await db.execute("DELETE FROM allocator_prefs WHERE item_id = $1", item_id)
    return {"ok": True}
