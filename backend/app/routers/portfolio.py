"""Trade ledger with FIFO cost basis and tax-aware profit and loss."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from .. import analytics, db, serial
from ..models import TradeIn

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/trades")
async def trades(item_id: Optional[int] = None, limit: int = Query(200, ge=1, le=1000)):
    args: list = []
    where = ""
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
    record = await db.fetchrow("SELECT tax_exempt FROM items WHERE id = $1", body.item_id)
    if record is None:
        raise HTTPException(404, "unknown item")
    # Tax is charged on sales only, and is recorded at execution time so later
    # rule changes do not rewrite history.
    tax = (
        analytics.sale_tax(body.price, record["tax_exempt"]) * body.quantity
        if body.side == "sell"
        else 0
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
        """SELECT t.item_id, t.side, t.quantity, t.price, t.tax_paid, t.executed_at,
                  i.name, i.icon, i.tax_exempt, i.buy_limit,
                  m.high, m.low, m.margin, m.price_change_24h
             FROM trades t
             JOIN items i ON i.id = t.item_id
             LEFT JOIN metrics m ON m.item_id = t.item_id
            ORDER BY t.executed_at, t.id"""
    )

    lots: dict[int, deque] = defaultdict(deque)
    realised: dict[int, int] = defaultdict(int)
    tax_paid: dict[int, int] = defaultdict(int)
    sold_qty: dict[int, int] = defaultdict(int)
    bought_qty: dict[int, int] = defaultdict(int)
    unmatched: dict[int, int] = defaultdict(int)
    meta: dict[int, dict] = {}

    for t in records:
        item_id = t["item_id"]
        meta[item_id] = {
            "item_id": item_id,
            "name": t["name"],
            "icon_url": serial.icon_url(t["icon"]),
            "high": t["high"],
            "low": t["low"],
            "tax_exempt": t["tax_exempt"],
            "price_change_24h": t["price_change_24h"],
        }
        if t["side"] == "buy":
            lots[item_id].append([t["quantity"], t["price"]])
            bought_qty[item_id] += t["quantity"]
            continue

        remaining = t["quantity"]
        sold_qty[item_id] += t["quantity"]
        # Tax was stored for the whole sale; spread it per unit for matching.
        unit_net = t["price"] - (t["tax_paid"] // t["quantity"] if t["quantity"] else 0)
        while remaining > 0 and lots[item_id]:
            lot = lots[item_id][0]
            take = min(remaining, lot[0])
            realised[item_id] += (unit_net - lot[1]) * take
            lot[0] -= take
            remaining -= take
            if lot[0] == 0:
                lots[item_id].popleft()
        if remaining > 0:
            # Sold more than the ledger shows buying; count revenue with no basis.
            realised[item_id] += unit_net * remaining
            unmatched[item_id] += remaining
        tax_paid[item_id] += t["tax_paid"]

    positions = []
    for item_id, info in meta.items():
        open_qty = sum(lot[0] for lot in lots[item_id])
        cost_basis = sum(lot[0] * lot[1] for lot in lots[item_id])
        avg_cost = cost_basis / open_qty if open_qty else None
        mark = info["high"]
        unrealised = None
        if open_qty and mark:
            exit_net = analytics.net_sale(mark, info["tax_exempt"])
            unrealised = exit_net * open_qty - cost_basis
        positions.append(
            {
                **info,
                "open_quantity": open_qty,
                "cost_basis": cost_basis,
                "avg_cost": avg_cost,
                "market_value": (mark or 0) * open_qty,
                "realised": realised[item_id],
                "unrealised": unrealised,
                "total": realised[item_id] + (unrealised or 0),
                "tax_paid": tax_paid[item_id],
                "bought_quantity": bought_qty[item_id],
                "sold_quantity": sold_qty[item_id],
                "unmatched_sales": unmatched[item_id],
            }
        )
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
