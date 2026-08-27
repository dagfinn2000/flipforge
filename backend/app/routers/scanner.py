from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from .. import db, policy, serial

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
    "fill": "m.est_fill_hours ASC NULLS LAST",
    "track": "t.track_score DESC NULLS LAST",
    "name": "i.name ASC",
}

# Note the absence of a raw spread column. Every margin the app reports is
# post-tax; there is deliberately no pre-tax figure to misread.
SELECT_ROW = """
SELECT i.id, i.name, i.icon, i.members, i.buy_limit, i.highalch,
       (x.item_id IS NOT NULL) AS tax_exempt,
       m.high, m.low, m.tax, m.margin, m.roi, m.breakeven_sell, m.crossed,
       m.vol_1h, m.vol_24h, m.flow_ratio, m.avg_margin_24h, m.margin_cv,
       m.margin_positive_24h, m.price_change_1h, m.price_change_24h, m.price_change_7d,
       m.volatility_24h, m.zscore_24h, m.vol_zscore, m.rsi_14,
       m.est_fill_hours, m.potential_profit, m.flip_score, m.data_age_seconds,
       t.track_score, t.samples AS track_samples, t.win_rate AS track_win_rate,
       t.median_cycle_profit AS track_median_profit
  FROM metrics m
  JOIN items i ON i.id = m.item_id
  LEFT JOIN tax_exemptions x ON x.item_id = m.item_id
  LEFT JOIN item_track_record t ON t.item_id = m.item_id
"""


@router.get("/scanner")
async def scanner(
    min_margin: int = Query(1),
    min_roi: float = Query(0.0, description="fraction, 0.02 == 2%"),
    min_volume: int = Query(200, description="units traded in the last 24h"),
    min_score: float = Query(0.0, ge=0, le=100),
    min_track_score: float = Query(
        0.0, ge=0, le=100,
        description="minimum trailing-month realised profitability score",
    ),
    max_price: Optional[int] = None,
    min_price: Optional[int] = None,
    min_buy_limit: Optional[int] = None,
    max_capital: Optional[int] = Query(None, description="gp available to spend"),
    members: Optional[bool] = None,
    max_age: int = Query(3600, description="max seconds since the quote was seen"),
    max_margin_cv: Optional[float] = Query(
        None, description="cap on margin variability; lower is steadier"
    ),
    max_fill_hours: Optional[float] = None,
    hide_crossed: bool = Query(False, description="hide items whose quotes are inverted"),
    sort: str = Query("score"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """The flip finder: every tradeable item ranked by a transparent score."""
    clauses, args = ["m.margin IS NOT NULL"], []

    def add(clause: str, value) -> None:
        args.append(value)
        clauses.append(clause.format(n=len(args)))

    add("m.margin >= ${n}", min_margin)
    add("COALESCE(m.roi, 0) >= ${n}", min_roi)
    add("COALESCE(m.vol_24h, 0) >= ${n}", min_volume)
    add("COALESCE(m.flip_score, 0) >= ${n}", min_score)
    if min_track_score > 0:
        add("COALESCE(t.track_score, 0) >= ${n}", min_track_score)
    add("COALESCE(m.data_age_seconds, 999999) <= ${n}", max_age)
    if max_price is not None:
        add("m.high <= ${n}", max_price)
    if min_price is not None:
        add("m.high >= ${n}", min_price)
    if min_buy_limit is not None:
        add("COALESCE(i.buy_limit, 0) >= ${n}", min_buy_limit)
    if members is not None:
        add("i.members = ${n}", members)
    if max_fill_hours is not None:
        add("COALESCE(m.est_fill_hours, 999) <= ${n}", max_fill_hours)
    if max_margin_cv is not None:
        add("COALESCE(m.margin_cv, 999) <= ${n}", max_margin_cv)
    if max_capital is not None:
        add("m.low <= ${n}", max_capital)
    if hide_crossed:
        clauses.append("NOT m.crossed")

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
    """Biggest percentage price moves over the chosen window."""
    column = {"1h": "price_change_1h", "24h": "price_change_24h", "7d": "price_change_7d"}[window]
    order = "DESC" if direction == "up" else "ASC"
    records = await db.fetch(
        f"""{SELECT_ROW}
            WHERE m.{column} IS NOT NULL
              AND COALESCE(m.vol_24h, 0) >= $1 AND COALESCE(m.high, 0) >= $2
            ORDER BY m.{column} {order} NULLS LAST LIMIT $3""",
        min_volume, min_price, limit,
    )
    return {"window": window, "direction": direction, "results": serial.rows(records)}


@router.get("/market/unusual")
async def unusual(
    min_volume: int = Query(500),
    signal: Optional[str] = Query(None, pattern="^(breakout|volume spike|thin move)$"),
    limit: int = Query(20, ge=1, le=100),
):
    """Items trading outside their own recent normal, split by whether volume agrees.

    A price move confirmed by volume is a real repricing. A price move on flat
    volume is usually one person pushing a shallow book, and conflating the two
    makes the whole feed useless.
    """
    records = await db.fetch(
        f"""{SELECT_ROW}
            WHERE COALESCE(m.vol_24h, 0) >= $1
              AND (ABS(COALESCE(m.zscore_24h, 0)) >= 2 OR COALESCE(m.vol_zscore, 0) >= 3)
            ORDER BY (LEAST(ABS(COALESCE(m.zscore_24h, 0)), 6)
                      + 1.5 * LEAST(GREATEST(COALESCE(m.vol_zscore, 0), 0), 6)) DESC
            LIMIT $2""",
        min_volume, limit * 3 if signal else limit,
    )
    out = []
    for r in serial.rows(records):
        pz, vz = r.get("zscore_24h") or 0, r.get("vol_zscore") or 0
        if abs(pz) >= 2 and vz >= 2:
            r["signal"] = "breakout"
            r["signal_note"] = "price and volume both broke their recent normal"
        elif vz >= 3:
            r["signal"] = "volume spike"
            r["signal_note"] = "unusual trading interest, price has not moved much yet"
        else:
            r["signal"] = "thin move"
            r["signal_note"] = "price moved without matching volume - treat with care"
        if signal is None or r["signal"] == signal:
            out.append(r)
    return {"results": out[:limit]}


@router.get("/market/summary")
async def summary():
    row = await db.fetchrow(
        """SELECT count(*) AS tracked,
                  count(*) FILTER (WHERE margin > 0) AS profitable,
                  count(*) FILTER (WHERE data_age_seconds <= 300) AS fresh,
                  count(*) FILTER (WHERE crossed) AS crossed,
                  COALESCE(SUM(vol_24h), 0) AS volume_24h,
                  COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY roi)
                           FILTER (WHERE margin > 0), 0) AS median_roi,
                  max(updated_at) AS updated_at
             FROM metrics"""
    )
    last_poll = await db.get_meta("last_latest_poll")
    return {
        **serial.row(row),
        "candles": await db.fetchval("SELECT count(*) FROM candles"),
        "backfill_complete": await db.get_meta("backfill_done") == "1",
        "last_poll": int(last_poll) if last_poll else None,
        "tax_policy": policy.describe(),
    }
