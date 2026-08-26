"""Record -> JSON helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

import asyncpg

from .config import settings

WIKI_IMAGES = "https://oldschool.runescape.wiki/images"


def icon_url(icon: Optional[str]) -> Optional[str]:
    if not icon:
        return None
    return f"{WIKI_IMAGES}/{icon.replace(' ', '_')}"


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return int(value.timestamp())
    return value


def row(record: Optional[asyncpg.Record], drop: Iterable[str] = ()) -> Optional[dict]:
    if record is None:
        return None
    out = {k: jsonable(v) for k, v in dict(record).items() if k not in set(drop)}
    if "icon" in out:
        out["icon_url"] = icon_url(out["icon"])
    return out


def rows(records: Iterable[asyncpg.Record], drop: Iterable[str] = ()) -> list[dict]:
    return [row(r, drop) for r in records]


def tax_config() -> dict:
    return {
        "rate": settings.ge_tax_rate,
        "cap": settings.ge_tax_cap,
        "min_price": settings.ge_tax_min_price,
    }
