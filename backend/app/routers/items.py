from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import analytics, db, ingest, serial
from ..wiki import VALID_TIMESTEPS

router = APIRouter(prefix="/api/items", tags=["items"])
log = logging.getLogger("flipforge.items")

ITEM_COLUMNS = """i.id, i.name, i.examine, i.members, i.value, i.lowalch, i.highalch,
                  i.buy_limit, i.icon, i.tax_exempt"""

# Per-item upstream history pulls are cached so a page refresh does not re-fetch.
_series_fetched: dict[tuple[int, str], float] = {}
_series_lock = asyncio.Lock()
SERIES_TTL = {"5m": 240, "1h": 1800, "6h": 7200, "24h": 43200}


@router.get("/search")
async def search(
    q: str = Query("", max_length=80),
    limit: int = Query(20, ge=1, le=100),
    members: Optional[bool] = None,
):
    """Fuzzy item search ranked by trigram similarity, then by liquidity."""
    term = q.strip().lower()
    clauses = []
    args: list = []
    if term:
        args.append(term)
        clauses.append(f"(lower(i.name) LIKE '%' || ${len(args)} || '%')")
    if members is not None:
        args.append(members)
        clauses.append(f"i.members = ${len(args)}")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = "similarity(lower(i.name), $1) DESC, " if term else ""
    args.append(limit)

    records = await db.fetch(
        f"""SELECT {ITEM_COLUMNS}, m.high, m.low, m.margin, m.roi, m.vol_24h,
                   m.flip_score, m.price_change_24h
              FROM items i LEFT JOIN metrics m ON m.item_id = i.id
              {where}
             ORDER BY {order}COALESCE(m.vol_24h, 0) DESC, i.name
             LIMIT ${len(args)}""",
        *args,
    )
    return {"results": serial.rows(records)}


@router.get("/{item_id}")
async def detail(item_id: int):
    record = await db.fetchrow(
        f"""SELECT {ITEM_COLUMNS}, m.*, l.high_time, l.low_time, l.fetched_at
              FROM items i
              LEFT JOIN metrics m ON m.item_id = i.id
              LEFT JOIN latest l ON l.item_id = i.id
             WHERE i.id = $1""",
        item_id,
    )
    if record is None:
        raise HTTPException(404, "unknown item")

    data = serial.row(record, drop=("item_id",))
    breakdown = analytics.flip_score(
        roi_value=record["roi"],
        margin_value=record["margin"],
        vol_24h=record["vol_24h"],
        margin_stability=record["margin_stability"],
        est_fill_hours=record["est_fill_hours"],
        data_age_seconds=record["data_age_seconds"],
        potential_profit=record["potential_profit"],
    )
    data["score_breakdown"] = {
        "roi": breakdown.roi, "profit": breakdown.profit, "volume": breakdown.volume,
        "stability": breakdown.stability, "fill": breakdown.fill,
        "freshness": breakdown.freshness, "total": breakdown.total,
        "notes": breakdown.notes, "weights": analytics.WEIGHTS,
    }
    data["watched"] = bool(
        await db.fetchval("SELECT 1 FROM watchlist WHERE item_id = $1", item_id)
    )
    data["tax_config"] = serial.tax_config()

    # Buying a full limit costs this much and returns this much post tax.
    limit = record["buy_limit"] or 0
    if limit and record["low"] and record["margin"]:
        data["limit_cycle"] = {
            "quantity": limit,
            "capital": limit * record["low"],
            "profit": limit * record["margin"],
        }
    return data


@router.get("/{item_id}/series")
async def series(
    item_id: int,
    timestep: str = Query("5m", pattern="^(5m|1h|6h|24h)$"),
    limit: int = Query(365, ge=10, le=1000),
):
    """Candles plus indicator overlays for the charting view."""
    if timestep not in VALID_TIMESTEPS:
        raise HTTPException(400, "bad timestep")
    if not await db.fetchval("SELECT 1 FROM items WHERE id = $1", item_id):
        raise HTTPException(404, "unknown item")

    await _ensure_series(item_id, timestep)
    records = await db.fetch(
        """SELECT ts, avg_high, avg_low, high_vol, low_vol
             FROM candles WHERE item_id = $1 AND timestep = $2
            ORDER BY ts DESC LIMIT $3""",
        item_id, timestep, limit,
    )
    records = list(reversed(records))
    exempt = await db.fetchval("SELECT tax_exempt FROM items WHERE id = $1", item_id)

    points = []
    mids: list[float] = []
    for r in records:
        high, low = r["avg_high"], r["avg_low"]
        mid = None
        if high and low:
            mid = (high + low) / 2
        elif high or low:
            mid = float(high or low)
        if mid is None:
            continue
        mids.append(mid)
        points.append(
            {
                "t": int(r["ts"].timestamp()),
                "high": high,
                "low": low,
                "mid": mid,
                "buy_vol": r["high_vol"],
                "sell_vol": r["low_vol"],
                "margin": analytics.margin(low, high, exempt) if high and low else None,
            }
        )

    volumes = [p["buy_vol"] + p["sell_vol"] for p in points]
    sma20 = analytics.sma(mids, 20)
    sma50 = analytics.sma(mids, 50)
    ema12 = analytics.ema(mids, 12)
    rsi14 = analytics.rsi(mids, 14)
    bb_mid, bb_up, bb_low = analytics.bollinger(mids, 20)
    vwap = analytics.vwap(mids, volumes)

    for i, p in enumerate(points):
        p["sma20"], p["sma50"], p["ema12"] = sma20[i], sma50[i], ema12[i]
        p["rsi"], p["vwap"] = rsi14[i], vwap[i]
        p["bb_upper"], p["bb_lower"] = bb_up[i], bb_low[i]

    return {
        "item_id": item_id,
        "timestep": timestep,
        "points": points,
        "volatility": analytics.volatility(mids),
    }


async def _ensure_series(item_id: int, timestep: str) -> None:
    """Lazily pull a year of upstream history the first time an item is opened."""
    key = (item_id, timestep)
    now = time.monotonic()
    async with _series_lock:
        last = _series_fetched.get(key)
        # `last is None` is the only "never fetched" signal available: monotonic()
        # counts from an arbitrary epoch, so a 0.0 default would read as "fetched
        # at boot" and suppress the first fetch on any host with less uptime than
        # the TTL.
        if last is not None and now - last < SERIES_TTL[timestep]:
            return
        _series_fetched[key] = now
    try:
        await ingest.fetch_item_series(item_id, timestep)
    except Exception as exc:  # noqa: BLE001 - stale local data still renders
        log.warning("series fetch failed for %s/%s: %s", item_id, timestep, exc)


@router.get("/{item_id}/calculator")
async def calculator(
    item_id: int,
    buy: Optional[int] = None,
    sell: Optional[int] = None,
    quantity: Optional[int] = None,
):
    """What a flip actually nets after the Grand Exchange takes its cut."""
    record = await db.fetchrow(
        """SELECT i.tax_exempt, i.buy_limit, m.high, m.low
             FROM items i LEFT JOIN metrics m ON m.item_id = i.id WHERE i.id = $1""",
        item_id,
    )
    if record is None:
        raise HTTPException(404, "unknown item")

    buy_price = buy if buy is not None else record["low"]
    sell_price = sell if sell is not None else record["high"]
    qty = quantity or record["buy_limit"] or 1
    if not buy_price or not sell_price:
        raise HTTPException(400, "no price available; pass buy and sell explicitly")

    unit_tax = analytics.sale_tax(sell_price, record["tax_exempt"])
    unit_margin = analytics.margin(buy_price, sell_price, record["tax_exempt"])
    return {
        "buy": buy_price,
        "sell": sell_price,
        "quantity": qty,
        "unit_tax": unit_tax,
        "unit_margin": unit_margin,
        "total_tax": unit_tax * qty,
        "capital_required": buy_price * qty,
        "gross_revenue": sell_price * qty,
        "net_revenue": (sell_price - unit_tax) * qty,
        "profit": unit_margin * qty,
        "roi": analytics.roi(buy_price, sell_price, record["tax_exempt"]),
        "buy_limit": record["buy_limit"],
        "over_limit": bool(record["buy_limit"] and qty > record["buy_limit"]),
        "tax_config": serial.tax_config(),
    }
