"""The flip score: six bounded components blended into one 0-100 number.

Every weight and saturation band lives at the top of this file so the model can
be argued with and changed in exactly one place. Each component returns its
value, weight and contribution, all of which are stored per item so the ranking
can be audited on screen rather than taken on trust.

Bounded is the point. An unbounded term lets one number own the ranking, which
is how "50% ROI" on a 1gp feather spread ends up above a real trade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

# --------------------------------------------------------------------- model --

WEIGHTS: dict[str, float] = {
    "roi": 0.22,          # is there an edge
    "profit": 0.26,       # is the edge worth real coins
    "liquidity": 0.18,    # does it actually trade
    "stability": 0.16,    # has the edge persisted, or is it a flicker
    "fill": 0.10,         # can a full limit be filled inside the window
    "freshness": 0.08,    # is the quote current
}

# Each band is (lo, hi): at or below lo scores 0, at or above hi scores 1, log
# scaled between. Bands beat plain log scaling, which hands out generous partial
# credit at the bottom -- 30k gp per cycle should read as negligible, not as
# "60% of the way to excellent".
ROI_BAND = (0.005, 0.08)              # 0.5% .. 8% post-tax
PROFIT_BAND = (10_000, 20_000_000)    # gp per 4h buy-limit cycle
LIQUIDITY_BAND = (100, 100_000)       # units traded per day

# Margin coefficient of variation: stdev of the spread over its own mean. At or
# below 0.35 the margin is steady; at or above 2.0 it only exists in flickers.
STABILITY_CV_GOOD = 0.35
STABILITY_CV_BAD = 2.0

FILL_TARGET_HOURS = 4.0               # one buy-limit window
FRESHNESS_STALE_SECONDS = 1800        # a 30 minute old quote scores zero

MAX_SCORE = 100.0


@dataclass
class Component:
    """One scored dimension, kept auditable."""

    key: str
    value: float          # normalised 0..1
    weight: float
    raw: Optional[float] = None   # the underlying measurement, for display

    @property
    def contribution(self) -> float:
        return MAX_SCORE * self.weight * self.value

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "weight": self.weight,
            "contribution": self.contribution,
            "raw": self.raw,
        }


@dataclass
class Score:
    total: float = 0.0
    components: list[Component] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "components": [c.as_dict() for c in self.components],
            "notes": self.notes,
            "weights": WEIGHTS,
        }

    def component(self, key: str) -> Optional[Component]:
        return next((c for c in self.components if c.key == key), None)


# ----------------------------------------------------------------- normalisers --

def clip01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def log_band(value: Optional[float], band: tuple[float, float]) -> float:
    """Map a value onto 0..1 across `band`, logarithmically."""
    lo, hi = band
    if not value or value <= lo:
        return 0.0
    return clip01((math.log10(value) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)))


def linear_band(value: Optional[float], lo: float, hi: float, invert: bool = False) -> float:
    """Map onto 0..1 across [lo, hi]; invert when lower is better."""
    if value is None:
        return 0.0
    if hi == lo:
        return 0.0
    t = clip01((value - lo) / (hi - lo))
    return 1.0 - t if invert else t


# --------------------------------------------------------------------- scoring --

def flip_score(
    *,
    roi: Optional[Decimal | float],
    margin: Optional[int],
    potential_profit: Optional[int],
    volume_24h: Optional[int],
    margin_cv: Optional[float],
    est_fill_hours: Optional[float],
    quote_age_seconds: Optional[int],
) -> Score:
    """Blend edge, size, liquidity, steadiness, fillability and freshness.

    A flip is worth doing when there is an edge (roi), the edge is worth real
    coins (profit), the item actually trades (liquidity), the edge has persisted
    rather than flickered (stability), a full buy limit can realistically be
    filled inside four hours (fill), and the quote is not stale (freshness).
    """
    score = Score()
    roi_value = float(roi) if roi is not None else None

    # An unprofitable flip is not ranked at all; it is simply not a flip.
    if not margin or margin <= 0 or not roi_value or roi_value <= 0:
        score.components = [Component(k, 0.0, w) for k, w in WEIGHTS.items()]
        if margin is not None and margin <= 0:
            score.notes.append("no post-tax edge at the current quote")
        return score

    # Nor is something nobody traded. A quoted spread on a silent market is a
    # price two people once agreed on, not an opportunity, and the remaining
    # components would otherwise still award it a middling score.
    if not volume_24h:
        score.components = [Component(k, 0.0, w) for k, w in WEIGHTS.items()]
        score.notes.append("no trades recorded in the last 24 hours")
        return score

    stability = (
        linear_band(margin_cv, STABILITY_CV_GOOD, STABILITY_CV_BAD, invert=True)
        if margin_cv is not None
        else 0.4  # unknown steadiness is treated as mediocre, never as good
    )

    fill = (
        linear_band(est_fill_hours, 0.0, FILL_TARGET_HOURS, invert=True)
        if est_fill_hours is not None
        else 0.3
    )

    age = quote_age_seconds if quote_age_seconds is not None else FRESHNESS_STALE_SECONDS
    freshness = linear_band(age, 0.0, FRESHNESS_STALE_SECONDS, invert=True)

    score.components = [
        Component("roi", log_band(roi_value, ROI_BAND), WEIGHTS["roi"], roi_value),
        Component("profit", log_band(potential_profit, PROFIT_BAND), WEIGHTS["profit"], potential_profit),
        Component("liquidity", log_band(volume_24h, LIQUIDITY_BAND), WEIGHTS["liquidity"], volume_24h),
        Component("stability", stability, WEIGHTS["stability"], margin_cv),
        Component("fill", fill, WEIGHTS["fill"], est_fill_hours),
        Component("freshness", freshness, WEIGHTS["freshness"], age),
    ]
    score.total = sum(c.contribution for c in score.components)

    if (volume_24h or 0) < 500:
        score.notes.append("thin market: under 500 units traded in 24h")
    if margin_cv is not None and margin_cv > STABILITY_CV_BAD:
        score.notes.append("margin is a flicker, not a level: it varies more than it averages")
    if potential_profit is not None and potential_profit < 50_000:
        score.notes.append("small absolute profit per cycle")
    if age > 900:
        score.notes.append("quote is over 15 minutes old")
    return score


# ------------------------------------------------------------------- capacity --

def est_fill_hours(buy_limit: Optional[int], hourly_volume: Optional[float]) -> Optional[float]:
    """Hours to buy a full limit assuming you capture ~25% of one side's flow."""
    if not buy_limit or not hourly_volume or hourly_volume <= 0:
        return None
    capture = hourly_volume * 0.25
    return min(buy_limit / capture, 999.0) if capture > 0 else None


def fillable_quantity(buy_limit: Optional[int], volume_24h: Optional[int]) -> int:
    """Units it is realistic to move in one 4 hour cycle.

    An item that did not trade at all yields zero. The buy limit is a ceiling on
    what the game permits, never evidence that a buyer exists: treating a silent
    market as fillable is how an item that has not traded in a day ends up
    advertising billions of gp per cycle.
    """
    volume = volume_24h or 0
    if volume <= 0:
        return 0
    limit = buy_limit if buy_limit and buy_limit > 0 else int(volume / 24)
    flow_cap = int(volume / 6 * 0.25)   # a quarter of a 4h window's flow
    return max(0, min(limit, flow_cap))


# ============================================================ track record ===
# The flip score says how good an item looks right now. This says how its flips
# have actually turned out over the past month, which is a different question
# and the only one with evidence behind it.

TRACK_WEIGHTS: dict[str, float] = {
    "win_rate": 0.55,   # how often a flip ended profitable
    "profit": 0.45,     # how much it paid when it did
}

# A coin flip is worth nothing, so the win-rate term only starts earning above
# 50% and saturates at 90%.
TRACK_WIN_FLOOR = 0.50
TRACK_WIN_CEILING = 0.90
# Same band as the forward-looking profit term, so the two are comparable.
TRACK_PROFIT_BAND = PROFIT_BAND
# Evidence discount: a 100% win rate over three samples is not a track record.
TRACK_FULL_CONFIDENCE_SAMPLES = 30


@dataclass
class TrackScore:
    total: float = 0.0
    confidence: float = 0.0
    components: list[Component] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def component_value(self, key: str) -> float:
        found = next((c for c in self.components if c.key == key), None)
        return found.value if found else 0.0

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "confidence": self.confidence,
            "components": [c.as_dict() for c in self.components],
            "notes": self.notes,
            "weights": TRACK_WEIGHTS,
        }


def track_score(
    *,
    samples: int,
    win_rate: Optional[float],
    median_cycle_profit: Optional[float],
) -> TrackScore:
    """Score an item on how its flips actually performed, not how they looked.

    Confidence multiplies rather than adds: thin evidence discounts the whole
    claim instead of counting as a separate virtue alongside it.
    """
    out = TrackScore()
    if not samples or win_rate is None:
        out.notes.append("no graded flips in the window")
        return out

    out.confidence = clip01(samples / TRACK_FULL_CONFIDENCE_SAMPLES)
    win_component = linear_band(win_rate, TRACK_WIN_FLOOR, TRACK_WIN_CEILING)
    profit_component = log_band(median_cycle_profit, TRACK_PROFIT_BAND)

    out.components = [
        Component("win_rate", win_component, TRACK_WEIGHTS["win_rate"], win_rate),
        Component("profit", profit_component, TRACK_WEIGHTS["profit"], median_cycle_profit),
    ]
    raw = sum(c.contribution for c in out.components)
    out.total = raw * out.confidence

    if samples < TRACK_FULL_CONFIDENCE_SAMPLES:
        out.notes.append(
            f"only {samples} graded flips: score discounted to "
            f"{out.confidence:.0%} confidence"
        )
    if win_rate < TRACK_WIN_FLOOR:
        out.notes.append("flips on this item lost more often than they won")
    if (median_cycle_profit or 0) <= 0:
        out.notes.append("the typical flip here ended flat or down")
    return out
