"""Trade ledger with FIFO cost basis and tax-aware profit and loss."""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import db, money, policy, serial
from ..models import TradeIn

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/trades")
async def trades(item_id: Optional[int] = None, limit: int = Query(200, ge=1, le=1000)):
    args, where = [], ""
    if item_id is not None:
        args.append(item_id)
        where = "WHERE t.item_id = $1"
    args.append(limit)
    records = await db.fetch(
        f"""SELECT t.*, i.name, i.icon FROM trades t JOIN items i ON i.id = t.item_id
            {where} ORDER BY t.executed_at DESC LIMIT ${len(args)}""",
        *args,
    )
    return {"results": serial.rows(records)}


@router.post("/trades")
async def add_trade(body: TradeIn):
    if not await db.fetchval("SELECT 1 FROM items WHERE id = $1", body.item_id):
        raise HTTPException(404, "unknown item")
    # Tax is recorded at execution time so a later rule change cannot rewrite
    # history: what you actually paid stays what you actually paid.
    tax = (
        money.sale_tax(body.price, policy.current(), body.item_id) * body.quantity
        if body.side == "sell" else 0
    )
    trade_id = await db.fetchval(
        """INSERT INTO trades (item_id, side, quantity, price, tax_paid, note, executed_at)
           VALUES ($1,$2,$3,$4,$5,$6, COALESCE($7, now())) RETURNING id""",
        body.item_id, body.side, body.quantity, body.price, tax, body.note, body.executed_at,
    )
    return {"ok": True, "id": trade_id, "tax_paid": tax}


@router.delete("/trades/{trade_id}")
async def delete_trade(trade_id: int):
    await db.execute("DELETE FROM trades WHERE id = $1", trade_id)
    return {"ok": True}


@router.get("")
async def portfolio():
    """Match sells against buys FIFO to split realised from open exposure."""
    records = await db.fetch(
        """SELECT t.item_id, t.side, t.quantity, t.price, t.executed_at,
                  i.name, i.icon, i.buy_limit, m.high, m.low, m.price_change_24h
             FROM trades t
             JOIN items i ON i.id = t.item_id
             LEFT JOIN metrics m ON m.item_id = t.item_id
            ORDER BY t.executed_at, t.id"""
    )

    tax = policy.current()
    fills: dict[int, list[money.Fill]] = defaultdict(list)
    meta: dict[int, dict] = {}
    for t in records:
        fills[t["item_id"]].append(
            money.Fill(t["side"], int(t["quantity"]), int(t["price"]),
                       int(t["executed_at"].timestamp()))
        )
        meta[t["item_id"]] = {
            "item_id": t["item_id"], "name": t["name"],
            "icon_url": serial.icon_url(t["icon"]), "high": t["high"], "low": t["low"],
            "price_change_24h": serial.jsonable(t["price_change_24h"]),
        }

    positions = []
    for item_id, item_fills in fills.items():
        result = money.match_fifo(item_fills, tax, item_id)
        mark = meta[item_id]["high"]
        unreal = money.unrealised(result, mark, tax, item_id)
        avg = result.average_cost
        positions.append({
            **meta[item_id],
            "open_quantity": result.open_quantity,
            "cost_basis": result.cost_basis,
            "avg_cost": float(avg) if avg is not None else None,
            "market_value": (mark or 0) * result.open_quantity,
            "realised": result.realised,
            "unrealised": unreal,
            "total": result.realised + (unreal or 0),
            "tax_paid": result.tax_paid,
            "bought_quantity": result.bought,
            "sold_quantity": result.sold,
            "unmatched_sales": result.unmatched_sales,
            "breakeven_sell": (
                money.breakeven_sell(int(avg), tax, item_id) if avg else None
            ),
        })
    positions.sort(key=lambda p: p["total"], reverse=True)

    totals = {
        "realised": sum(p["realised"] for p in positions),
        "unrealised": sum(p["unrealised"] or 0 for p in positions),
        "capital_deployed": sum(p["cost_basis"] for p in positions),
        "market_value": sum(p["market_value"] for p in positions),
        "tax_paid": sum(p["tax_paid"] for p in positions),
        "open_positions": sum(1 for p in positions if p["open_quantity"] > 0),
        "trades": len(records),
    }
    totals["total"] = totals["realised"] + totals["unrealised"]
    totals["return_pct"] = (
        totals["total"] / totals["capital_deployed"] if totals["capital_deployed"] else None
    )
    return {"positions": positions, "totals": totals}
