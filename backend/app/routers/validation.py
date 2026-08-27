"""Does the flip score actually predict anything?

Every hour the model's claim is frozen into score_snapshots. Once a snapshot
matures, score_outcomes records what a flip entered at that moment would really
have banked after tax. This router reads the two back together, so the answer to
"is a score of 85 better than a score of 40" is a measurement rather than an
opinion. If the curve is flat, the model is decoration and you can see it here.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from .. import db, serial

router = APIRouter(prefix="/api/validation", tags=["validation"])

HORIZONS = ("1h", "4h", "24h")

DECILE_SQL = """
WITH graded AS (
    SELECT s.score, s.buy, s.quantity,
           o.realised_margin, o.realised_cycle_profit, o.realised_roi
      FROM score_snapshots s
      JOIN score_outcomes o ON o.item_id = s.item_id AND o.ts = s.ts
     WHERE o.horizon = $1
       AND s.ts > now() - ($2 || ' days')::interval
       AND s.score IS NOT NULL
       AND ($3::text IS NULL OR s.source = $3)
),
bucketed AS (
    SELECT ntile(10) OVER (ORDER BY score) AS decile, * FROM graded
)
SELECT decile,
       count(*)                                             AS samples,
       round(min(score), 1)                                 AS score_min,
       round(max(score), 1)                                 AS score_max,
       round(avg(score), 1)                                 AS score_avg,
       round(avg(realised_margin))                          AS avg_realised_margin,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY realised_margin))
                                                            AS median_realised_margin,
       -- The unit that matters: per-unit margin times what you could actually
       -- buy in one 4 hour cycle.
       round(avg(realised_cycle_profit))                    AS avg_cycle_profit,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY realised_cycle_profit))
                                                            AS median_cycle_profit,
       round(avg(realised_roi), 6)                          AS avg_realised_roi,
       round(avg(CASE WHEN realised_margin > 0 THEN 1.0 ELSE 0.0 END), 4) AS win_rate
  FROM bucketed
 GROUP BY decile
 ORDER BY decile
"""


@router.get("/deciles")
async def deciles(
    horizon: str = Query("4h", pattern="^(1h|4h|24h)$"),
    days: int = Query(14, ge=1, le=90),
    source: Optional[str] = Query(
        None, pattern="^(live|reconstructed)$",
        description="restrict to snapshots frozen live, or to ones rebuilt from history",
    ),
):
    """Realised post-tax return bucketed by score decile."""
    records = await db.fetch(DECILE_SQL, horizon, str(days), source)
    rows = serial.rows(records)

    verdict = None
    if len(rows) >= 10:
        bottom, top = rows[0], rows[-1]
        lift = (top["avg_cycle_profit"] or 0) - (bottom["avg_cycle_profit"] or 0)
        # Spearman-style check: does cycle profit actually climb with the score,
        # or does it only climb for a while and then fall back?
        profits = [r["median_cycle_profit"] or 0 for r in rows]
        best_decile = max(range(len(profits)), key=lambda i: profits[i]) + 1
        verdict = {
            "top_decile_avg_cycle_profit": top["avg_cycle_profit"],
            "bottom_decile_avg_cycle_profit": bottom["avg_cycle_profit"],
            "lift": lift,
            "top_beats_bottom": lift > 0,
            "top_win_rate": top["win_rate"],
            "bottom_win_rate": bottom["win_rate"],
            "best_decile_by_median_profit": best_decile,
            "monotonic_win_rate": all(
                (rows[i]["win_rate"] or 0) <= (rows[i + 1]["win_rate"] or 0)
                for i in range(len(rows) - 1)
            ),
        }
    mix = await db.fetch(
        """SELECT source, count(*) AS snapshots FROM score_snapshots
            WHERE ts > now() - ($1 || ' days')::interval GROUP BY source""",
        str(days),
    )
    return {
        "horizon": horizon, "days": days, "source": source,
        "deciles": rows, "verdict": verdict,
        "sources": {r["source"]: r["snapshots"] for r in mix},
    }


@router.get("/summary")
async def summary(days: int = Query(14, ge=1, le=90)):
    """Coverage and the headline correlation, per horizon."""
    out = []
    for horizon in HORIZONS:
        row = await db.fetchrow(
            """SELECT count(*) AS samples,
                      count(DISTINCT o.item_id) AS items,
                      min(s.ts) AS earliest,
                      max(s.ts) AS latest,
                      corr(s.score::double precision,
                           o.realised_roi::double precision) AS score_roi_corr,
                      corr(s.score::double precision,
                           o.realised_margin::double precision) AS score_margin_corr,
                      round(avg(o.realised_roi), 6) AS avg_realised_roi,
                      corr(s.score::double precision,
                           o.realised_cycle_profit::double precision) AS score_cycle_corr
                 FROM score_snapshots s
                 JOIN score_outcomes o ON o.item_id = s.item_id AND o.ts = s.ts
                WHERE o.horizon = $1 AND s.ts > now() - ($2 || ' days')::interval""",
            horizon, str(days),
        )
        out.append({"horizon": horizon, **serial.row(row)})

    pending = await db.fetchval(
        """SELECT count(*) FROM score_snapshots s
            WHERE NOT EXISTS (SELECT 1 FROM score_outcomes o
                               WHERE o.item_id = s.item_id AND o.ts = s.ts)"""
    )
    snapshots = await db.fetchval("SELECT count(*) FROM score_snapshots")
    return {
        "days": days,
        "horizons": out,
        "snapshots_total": snapshots,
        "snapshots_awaiting_grade": pending,
        "note": (
            "Realised margin is what a flip bought at the snapshot's instant-sell "
            "price and exited at the item's average instant-buy price one horizon "
            "later would have banked, after tax. It assumes both sides fill, which "
            "is the optimistic case."
        ),
    }


@router.get("/history")
async def history(
    item_id: int = Query(..., description="item to inspect"),
    horizon: str = Query("4h", pattern="^(1h|4h|24h)$"),
    limit: int = Query(100, ge=1, le=500),
):
    """Every graded snapshot for one item: what was claimed against what happened."""
    records = await db.fetch(
        """SELECT s.ts, s.score, s.buy, s.sell, s.margin AS predicted_margin,
                  o.exit_price, o.realised_margin, o.realised_roi
             FROM score_snapshots s
             JOIN score_outcomes o ON o.item_id = s.item_id AND o.ts = s.ts
            WHERE s.item_id = $1 AND o.horizon = $2
            ORDER BY s.ts DESC LIMIT $3""",
        item_id, horizon, limit,
    )
    return {"item_id": item_id, "horizon": horizon, "results": serial.rows(records)}
