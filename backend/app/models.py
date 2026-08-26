from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class WatchIn(BaseModel):
    item_id: int
    note: Optional[str] = None


class AlertIn(BaseModel):
    item_id: int
    metric: Literal["high", "low", "margin", "roi", "vol_1h", "zscore_24h", "flip_score"]
    op: Literal["above", "below"]
    threshold: float
    note: Optional[str] = None
    cooldown_s: int = Field(default=900, ge=60, le=86400)


class TradeIn(BaseModel):
    item_id: int
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0)
    price: int = Field(ge=0)
    note: Optional[str] = None
    executed_at: Optional[datetime] = None
