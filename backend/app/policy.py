"""Loads the tax policy from config plus the editable exemptions table.

Business logic never sees a hardcoded rate, cap or exemption. Change a value in
.env or a row in tax_exemptions, restart the API, and every margin, ROI, score
and portfolio figure in the app follows.
"""

from __future__ import annotations

import logging

from . import db
from .config import settings
from .money import TaxPolicy

log = logging.getLogger("flipforge.policy")

# Best-effort seed of the items the Grand Exchange does not tax, matched to ids
# by name against the mapping. Treat it as a starting point, not gospel: it is a
# plain table and the API exposes add/remove so a game update is a row change.
SEED_EXEMPT_NAMES = [
    "Old school bond",
    "Chisel",
    "Gardening trowel",
    "Glassblowing pipe",
    "Gloves of silence",
    "Hammer",
    "Needle",
    "Rake",
    "Saw",
    "Secateurs",
    "Seed dibber",
    "Shears",
    "Spade",
    "Watering can",
]

_policy = TaxPolicy(rate=settings.ge_tax_rate, cap=settings.ge_tax_cap)


def current() -> TaxPolicy:
    """The policy in force. Cheap enough to call per row."""
    return _policy


async def seed_exemptions() -> int:
    """Insert any seed names that resolve to a known item and are not yet listed."""
    if not settings.seed_tax_exemptions:
        return 0
    rows = await db.fetch(
        """SELECT id, name FROM items
            WHERE lower(name) = ANY($1::text[])""",
        [n.lower() for n in SEED_EXEMPT_NAMES],
    )
    if not rows:
        return 0
    await db.executemany(
        """INSERT INTO tax_exemptions (item_id, name, source)
           VALUES ($1, $2, 'seed') ON CONFLICT (item_id) DO NOTHING""",
        [(r["id"], r["name"]) for r in rows],
    )
    missing = {n.lower() for n in SEED_EXEMPT_NAMES} - {r["name"].lower() for r in rows}
    if missing:
        log.info("exemption seed: no mapping entry for %s", sorted(missing))
    return len(rows)


async def reload() -> TaxPolicy:
    """Rebuild the in-memory policy from the database."""
    global _policy
    ids = await db.fetch("SELECT item_id FROM tax_exemptions")
    _policy = TaxPolicy(
        rate=settings.ge_tax_rate,
        cap=settings.ge_tax_cap,
        exempt_item_ids=frozenset(r["item_id"] for r in ids),
    )
    log.info(
        "tax policy: %s%% capped at %s gp, free below %s gp, %s exempt items",
        _policy.rate * 100, f"{_policy.cap:,}", _policy.free_below, len(_policy.exempt_item_ids),
    )
    return _policy


def describe() -> dict:
    """Policy as JSON for the UI, so the rules in force are always visible."""
    p = current()
    return {
        "rate": float(p.rate),
        "cap": p.cap,
        "free_below": p.free_below,
        "exempt_count": len(p.exempt_item_ids),
        "note": (
            f"{p.rate * 100:g}% of the sale price, floored per item, capped at "
            f"{p.cap:,} gp. Sales under {p.free_below} gp round down to no tax."
        ),
    }
