from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import db, indicators, ingest, money, policy, serial
from ..wiki import TIMESTEPS

router = APIRouter(prefix="/api/items", tags=["items"])
log = logging.getLogger("flipforge.items")

ITEM_COLUMNS = """i.id, i.name, i.examine, i.members, i.value, i.lowalch, i.highalch,
                  i.buy_limit, i.icon"""

# Per-item upstream history pulls are cached so a page refresh does not refetch.
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
    clauses, args = [], []
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
                   m.flip_score, m.price_change_24h, m.crossed
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
        f"""SELECT {ITEM_COLUMNS}, m.*, l.high_time, l.low_time, l.fetched_at,
                   (x.item_id IS NOT NULL) AS tax_exempt
              FROM items i
              LEFT JOIN metrics m ON m.item_id = i.id
              LEFT JOIN latest l ON l.item_id = i.id
              LEFT JOIN tax_exemptions x ON x.item_id = i.id
             WHERE i.id = $1""",
        item_id,
    )
    if record is None:
        raise HTTPException(404, "unknown item")

    data = serial.row(record, drop=("item_id",))
    tax = policy.current()

    # The stored breakdown is what actually produced the ranking, so the page
    # shows the model's own working rather than a recomputation of it.
    components = record["score_components"]
    data["score_breakdown"] = json.loads(components) if isinstance(components, str) else components

    data["watched"] = bool(await db.fetchval("SELECT 1 FROM watchlist WHERE item_id = $1", item_id))
    data["tax_policy"] = policy.describe()

    # Rolling 4 hour buy limit state, from the trades ledger.
    purchases = await db.fetch(
        """SELECT quantity, executed_at FROM trades
            WHERE item_id = $1 AND side = 'buy'
              AND executed_at > now() - INTERVAL '4 hours'""",
        item_id,
    )
    window = money.limit_window(
        record["buy_limit"],
        [money.Purchase(int(p["quantity"]), int(p["executed_at"].timestamp())) for p in purchases],
        int(time.time()),
    )
    data["limit_window"] = {
        "limit": window.limit, "used": window.used, "remaining": window.remaining,
        "resets_at": window.resets_at, "window_hours": window.window_seconds // 3600,
    }

    if record["low"] and record["margin"] is not None and window.limit:
        qty = window.remaining if window.remaining is not None else window.limit
        data["limit_cycle"] = {
            "quantity": qty,
            "capital": qty * record["low"],
            "profit": qty * record["margin"],
        }
    return data


@router.get("/{item_id}/series")
async def series(
    item_id: int,
    timestep: str = Query("5m", pattern="^(5m|1h|6h|24h)$"),
    limit: int = Query(365, ge=10, le=1000),
):
    """Candles plus indicator overlays for the charting view."""
    if timestep not in TIMESTEPS:
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
    tax = policy.current()

    points, mids = [], []
    for r in records:
        high = int(r["avg_high"]) if r["avg_high"] is not None else None
        low = int(r["avg_low"]) if r["avg_low"] is not None else None
        mid = (high + low) / 2 if (high and low) else float(high or low or 0) or None
        if mid is None:
            continue
        mids.append(mid)
        points.append({
            "t": int(r["ts"].timestamp()), "high": high, "low": low, "mid": mid,
            "buy_vol": r["high_vol"], "sell_vol": r["low_vol"],
            "margin": money.margin(low, high, tax, item_id) if (high and low) else None,
            "crossed": money.is_crossed(low, high),
        })

    volumes = [p["buy_vol"] + p["sell_vol"] for p in points]
    sma20, sma50 = indicators.sma(mids, 20), indicators.sma(mids, 50)
    ema12, rsi14 = indicators.ema(mids, 12), indicators.rsi(mids, 14)
    _, bb_up, bb_low = indicators.bollinger(mids, 20)
    vwap = indicators.vwap(mids, volumes)

    for i, p in enumerate(points):
        p["sma20"], p["sma50"], p["ema12"] = sma20[i], sma50[i], ema12[i]
        p["rsi"], p["vwap"] = rsi14[i], vwap[i]
        p["bb_upper"], p["bb_lower"] = bb_up[i], bb_low[i]

    return {
        "item_id": item_id, "timestep": timestep, "points": points,
        "volatility": indicators.volatility(mids),
        "crossed_count": sum(1 for p in points if p["crossed"]),
    }


async def _ensure_series(item_id: int, timestep: str) -> None:
    """Lazily pull deep history the first time an item is opened."""
    key = (item_id, timestep)
    now = time.monotonic()
    async with _series_lock:
        last = _series_fetched.get(key)
        # `last is None` is the only "never fetched" signal available: monotonic()
        # counts from an arbitrary epoch, so a 0.0 default would read as "fetched
        # at boot" and suppress the first fetch on a freshly booted host.
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
        """SELECT i.buy_limit, m.high, m.low
             FROM items i LEFT JOIN metrics m ON m.item_id = i.id WHERE i.id = $1""",
        item_id,
    )
    if record is None:
        raise HTTPException(404, "unknown item")

    tax = policy.current()
    buy_price = buy if buy is not None else record["low"]
    sell_price = sell if sell is not None else record["high"]
    qty = quantity or record["buy_limit"] or 1
    if not buy_price or not sell_price:
        raise HTTPException(400, "no price available; pass buy and sell explicitly")

    unit_tax = money.sale_tax(sell_price, tax, item_id)
    unit_margin = money.margin(buy_price, sell_price, tax, item_id)
    breakeven = money.breakeven_sell(buy_price, tax, item_id)
    roi = money.roi(buy_price, sell_price, tax, item_id)
    return {
        "buy": buy_price, "sell": sell_price, "quantity": qty,
        "unit_tax": unit_tax, "unit_margin": unit_margin,
        "total_tax": unit_tax * qty,
        "capital_required": buy_price * qty,
        "net_revenue": money.net_received(sell_price, qty, tax, item_id),
        "profit": unit_margin * qty if unit_margin is not None else None,
        "roi": float(roi) if roi is not None else None,
        "breakeven_sell": breakeven,
        "breakeven_uplift": (breakeven - buy_price) if breakeven else None,
        "buy_limit": record["buy_limit"],
        "over_limit": bool(record["buy_limit"] and qty > record["buy_limit"]),
        "crossed": money.is_crossed(buy_price, sell_price),
        "tax_policy": policy.describe(),
    }
