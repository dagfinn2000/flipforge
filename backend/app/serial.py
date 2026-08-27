"""Record -> JSON helpers."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Optional

import asyncpg

WIKI_IMAGES = "https://oldschool.runescape.wiki/images"


def icon_url(icon: Optional[str]) -> Optional[str]:
    return f"{WIKI_IMAGES}/{icon.replace(' ', '_')}" if icon else None


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, Decimal):
        # NUMERIC keeps money exact in the database; JSON has no decimal type, so
        # it crosses the wire as a float and is formatted for display client side.
        return float(value)
    return value


def row(record: Optional[asyncpg.Record], drop: Iterable[str] = ()) -> Optional[dict]:
    if record is None:
        return None
    skip = set(drop)
    out = {k: jsonable(v) for k, v in dict(record).items() if k not in skip}
    if "icon" in out:
        out["icon_url"] = icon_url(out["icon"])
    return out


def rows(records: Iterable[asyncpg.Record], drop: Iterable[str] = ()) -> list[dict]:
    return [row(r, drop) for r in records]
