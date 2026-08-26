from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from .. import db, serial

router = APIRouter(prefix="/api", tags=["market"])

SORTS = {
    "score": "m.flip_score DESC NULLS LAST",
    "profit": "m.potential_profit DESC NULLS LAST",
    "roi": "m.roi DESC NULLS LAST",
    "margin": "m.margin DESC NULLS LAST",
    "volume": "m.vol_24h DESC NULLS LAST",
    "price": "m.high DESC NULLS LAST",
    "change_1h": "m.price_change_1h DESC NULLS LAST",
    "change_24h": "m.price_change_24h DESC NULLS LAST",
    "volatility": "m.volatility_24h DESC NULLS LAST",
    "name": "i.name ASC",
}

SELECT_ROW = """
SELECT i.id, i.name, i.icon, i.members, i.buy_limit, i.tax_exempt,
       i.highalch, m.high, m.low, m.spread, m.tax, m.margin, m.roi,
       m.vol_1h, m.vol_24h, m.flow_ratio, m.avg_margin_24h, m.margin_stability,
       m.price_change_1h, m.price_change_24h, m.price_change_7d,
       m.volatility_24h, m.zscore_24h, m.vol_zscore, m.rsi_14,
       m.est_fill_hours, m.potential_profit, m.flip_score, m.data_age_seconds
  FROM metrics m JOIN items i ON i.id = m.item_id
"""


@router.get("/scanner")
async def scanner(
    min_margin: int = Query(1),
    min_roi: float = Query(0.0, description="fraction, 0.02 == 2%"),
    min_volume: int = Query(200, description="units traded in the last 24h"),
    max_price: Optional[int] = None,
    min_price: Optional[int] = None,
    max_capital: Optional[int] = Query(None, description="gp available to spend"),
    members: Optional[bool] = None,
    max_age: int = Query(3600, description="max seconds since the quote was seen"),
    min_stability: float = Query(0.0, ge=0.0, le=1.0),
    max_fill_hours: Optional[float] = None,
    sort: str = Query("score"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """The flip finder: every tradeable item ranked by a transparent score."""
    clauses = ["m.margin IS NOT NULL"]
    args: list = []

    def add(clause: str, value) -> None:
        args.append(value)
        clauses.append(clause.format(n=len(args)))

    add("m.margin >= ${n}", min_margin)
    add("COALESCE(m.roi, 0) >= ${n}", min_roi)
    add("COALESCE(m.vol_24h, 0) >= ${n}", min_volume)
    add("COALESCE(m.data_age_seconds, 999999) <= ${n}", max_age)
    add("COALESCE(m.margin_stability, 0) >= ${n}", min_stability)
    if max_price is not None:
        add("m.high <= ${n}", max_price)
    if min_price is not None:
        add("m.high >= ${n}", min_price)
    if members is not None:
        add("i.members = ${n}", members)
    if max_fill_hours is not None:
        add("COALESCE(m.est_fill_hours, 999) <= ${n}", max_fill_hours)
    if max_capital is not None:
        # Must be able to afford at least one unit, and the ranking below
        # reflects what is actually affordable rather than a theoretical limit.
        add("m.low <= ${n}", max_capital)

    order = SORTS.get(sort, SORTS["score"])
    args.extend([limit, offset])
    records = await db.fetch(
        f"""{SELECT_ROW} WHERE {' AND '.join(clauses)}
            ORDER BY {order} LIMIT ${len(args) - 1} OFFSET ${len(args)}""",
        *args,
    )
    results = serial.rows(records)

    if max_capital is not None:
        for r in results:
            affordable = max_capital // r["low"] if r["low"] else 0
            capped = min(affordable, r["buy_limit"] or affordable)
            r["affordable_quantity"] = capped
            r["affordable_profit"] = capped * (r["margin"] or 0)

    return {"results": results, "count": len(results), "sort": sort}


@router.get("/market/movers")
async def movers(
    window: str = Query("24h", pattern="^(1h|24h|7d)$"),
    direction: str = Query("up", pattern="^(up|down)$"),
    min_volume: int = Query(1000),
    min_price: int = Query(
        100, description="ignore items cheaper than this; sub-100gp prices are "
                         "quantised to single coins and produce meaningless percentages"
    ),
    limit: int = Query(15, ge=1, le=100),
):
    """Biggest percentage price moves over the chosen window.

    Cheap items are excluded by default: a mithril dagger drifting from 1gp to
    30gp is a genuine +2900% and completely useless as a signal.
    """
    column = {"1h": "price_change_1h", "24h": "price_change_24h", "7d": "price_change_7d"}[window]
    order = "DESC" if direction == "up" else "ASC"
    records = await db.fetch(
        f"""{SELECT_ROW}
            WHERE m.{column} IS NOT NULL
              AND COALESCE(m.vol_24h, 0) >= $1
              AND COALESCE(m.high, 0) >= $2
            ORDER BY m.{column} {order} NULLS LAST LIMIT $3""",
        min_volume, min_price, limit,
    )
    return {"window": window, "direction": direction, "results": serial.rows(records)}


@router.get("/market/unusual")
async def unusual(
    min_volume: int = Query(500),
    limit: int = Query(20, ge=1, le=100),
):
    """Items whose price or volume has broken out of its own recent normal.

    A large price z-score with an even larger volume z-score is the signature of
    a real move; a large price move on flat volume is more often a thin book
    being pushed around.
    """
    records = await db.fetch(
        f"""{SELECT_ROW}
            WHERE COALESCE(m.vol_24h, 0) >= $1
              AND (ABS(COALESCE(m.zscore_24h, 0)) >= 2 OR COALESCE(m.vol_zscore, 0) >= 3)
            -- Cap the price term so one wild reading cannot own the list, and
            -- weight volume confirmation above raw price movement.
            ORDER BY (LEAST(ABS(COALESCE(m.zscore_24h, 0)), 6)
                      + 1.5 * LEAST(GREATEST(COALESCE(m.vol_zscore, 0), 0), 6)) DESC
            LIMIT $2""",
        min_volume, limit,
    )
    out = serial.rows(records)
    for r in out:
        pz, vz = r.get("zscore_24h") or 0, r.get("vol_zscore") or 0
        if abs(pz) >= 2 and vz >= 2:
            r["signal"] = "breakout"
            r["signal_note"] = "price and volume both broke their 24h normal"
        elif vz >= 3:
            r["signal"] = "volume spike"
            r["signal_note"] = "unusual trading interest, price has not moved much yet"
        else:
            r["signal"] = "thin move"
            r["signal_note"] = "price moved without matching volume - treat with care"
    return {"results": out}


@router.get("/market/summary")
async def summary():
    row = await db.fetchrow(
        """SELECT count(*) AS tracked,
                  count(*) FILTER (WHERE margin > 0) AS profitable,
                  count(*) FILTER (WHERE data_age_seconds <= 300) AS fresh,
                  COALESCE(SUM(vol_24h), 0) AS volume_24h,
                  -- A mean ROI is dominated by a handful of 200% outliers on
                  -- near-worthless items; the median describes the market.
                  COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY roi)
                           FILTER (WHERE margin > 0), 0) AS median_roi,
                  max(updated_at) AS updated_at
             FROM metrics"""
    )
    candles = await db.fetchval("SELECT count(*) FROM candles")
    backfilled = await db.get_meta("backfill_done") == "1"
    last_poll = await db.get_meta("last_latest_poll")
    return {
        **serial.row(row),
        "candles": candles,
        "backfill_complete": backfilled,
        "last_poll": int(last_poll) if last_poll else None,
        "tax_config": serial.tax_config(),
    }
