from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

AlertMetric = Literal["high", "low", "margin", "roi", "vol_1h", "zscore_24h", "flip_score"]


class WatchIn(BaseModel):
    item_id: int
    note: Optional[str] = None


class AlertIn(BaseModel):
    item_id: int
    metric: AlertMetric
    op: Literal["above", "below"]
    threshold: float
    # How far the value must retreat past the threshold before the alert re-arms.
    hysteresis: float = Field(default=0.0, ge=0.0)
    note: Optional[str] = None
    cooldown_s: int = Field(default=900, ge=60, le=86400)


class TradeIn(BaseModel):
    item_id: int
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0)
    price: int = Field(ge=0)
    note: Optional[str] = None
    executed_at: Optional[datetime] = None


class ExemptionIn(BaseModel):
    item_id: int
    note: Optional[str] = None


class AllocatorIn(BaseModel):
    """Inputs for the slot allocator."""

    bankroll: int = Field(gt=0, description="coins available to deploy")
    slots: int = Field(default=8, ge=1, le=8)
    min_volume: int = Field(default=1000, ge=0)
    min_score: float = Field(default=0.0, ge=0, le=100)
    max_quote_age: int = Field(default=1800, ge=60)
    members: Optional[bool] = None
    # No single item may absorb more than this share of the bankroll.
    max_share: float = Field(default=0.35, gt=0, le=1.0)
    pinned: list[int] = Field(default_factory=list)
    excluded: list[int] = Field(default_factory=list)


class AllocatorPrefIn(BaseModel):
    item_id: int
    mode: Literal["pin", "exclude"]
