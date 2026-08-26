"""Market maths: Grand Exchange tax, flip margins, indicators and scoring.

Everything here is pure and side-effect free so it can be unit tested and reused
by both the ingest rollup and the per-item detail endpoint.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import fmean, pstdev
from typing import Iterable, Optional, Sequence

from .config import settings

# Items Jagex exempts from the sale tax. Matched case-insensitively by name.
TAX_EXEMPT_NAMES = {
    "old school bond",
    "chisel",
    "gardening trowel",
    "glassblowing pipe",
    "hammer",
    "needle",
    "rake",
    "saw",
    "secateurs",
    "seed dibber",
    "shears",
    "spade",
    "watering can",
}


def is_tax_exempt(name: str) -> bool:
    return name.strip().lower() in TAX_EXEMPT_NAMES


def sale_tax(price: Optional[int], exempt: bool = False) -> int:
    """Tax deducted from the seller when one item sells at `price`.

    Rate, cap and the minimum taxable price all come from settings because Jagex
    has changed them before and self-hosters should not need a code edit.
    """
    if not price or price < settings.ge_tax_min_price or exempt:
        return 0
    return min(int(price * settings.ge_tax_rate), settings.ge_tax_cap)


def net_sale(price: Optional[int], exempt: bool = False) -> int:
    """Coins actually received after the Grand Exchange takes its cut."""
    if not price:
        return 0
    return price - sale_tax(price, exempt)


def margin(buy: Optional[int], sell: Optional[int], exempt: bool = False) -> Optional[int]:
    """Post-tax profit per unit for buying at `buy` and selling at `sell`."""
    if not buy or not sell:
        return None
    return net_sale(sell, exempt) - buy


def roi(buy: Optional[int], sell: Optional[int], exempt: bool = False) -> Optional[float]:
    m = margin(buy, sell, exempt)
    if m is None or not buy:
        return None
    return m / buy


# --------------------------------------------------------------- indicators --

def sma(values: Sequence[float], window: int) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= window:
            total -= values[i - window]
        out.append(total / window if i >= window - 1 else None)
    return out


def ema(values: Sequence[float], window: int) -> list[Optional[float]]:
    if not values:
        return []
    k = 2 / (window + 1)
    out: list[Optional[float]] = [None] * len(values)
    run: Optional[float] = None
    for i, v in enumerate(values):
        run = v if run is None else v * k + run * (1 - k)
        if i >= window - 1:
            out[i] = run
    return out


def rsi(values: Sequence[float], window: int = 14) -> list[Optional[float]]:
    """Wilder's RSI. Returns None until there are enough points to seed it."""
    n = len(values)
    out: list[Optional[float]] = [None] * n
    if n <= window:
        return out
    gains = losses = 0.0
    for i in range(1, window + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / window, losses / window
    out[window] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(window + 1, n):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (window - 1) + max(change, 0.0)) / window
        avg_loss = (avg_loss * (window - 1) + max(-change, 0.0)) / window
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def bollinger(values: Sequence[float], window: int = 20, mult: float = 2.0):
    mid = sma(values, window)
    upper: list[Optional[float]] = []
    lower: list[Optional[float]] = []
    for i, m in enumerate(mid):
        if m is None:
            upper.append(None)
            lower.append(None)
            continue
        sd = pstdev(values[i - window + 1 : i + 1])
        upper.append(m + mult * sd)
        lower.append(m - mult * sd)
    return mid, upper, lower


def vwap(prices: Sequence[Optional[float]], volumes: Sequence[float]) -> list[Optional[float]]:
    """Running volume weighted average price over the supplied window."""
    out: list[Optional[float]] = []
    pv = vol = 0.0
    for p, v in zip(prices, volumes):
        if p is not None and v:
            pv += p * v
            vol += v
        out.append(pv / vol if vol else None)
    return out


def log_returns(values: Sequence[float]) -> list[float]:
    out = []
    for prev, cur in zip(values, values[1:]):
        if prev > 0 and cur > 0:
            out.append(math.log(cur / prev))
    return out


def volatility(values: Sequence[float]) -> Optional[float]:
    """Standard deviation of log returns -- a unitless comparable risk measure."""
    rets = log_returns(values)
    return pstdev(rets) if len(rets) > 2 else None


def zscore(current: Optional[float], history: Sequence[float]) -> Optional[float]:
    if current is None or len(history) < 5:
        return None
    sd = pstdev(history)
    if sd == 0:
        return 0.0
    return (current - fmean(history)) / sd


def pct_change(old: Optional[float], new: Optional[float]) -> Optional[float]:
    if not old or new is None:
        return None
    return (new - old) / old


# ------------------------------------------------------------------ scoring --

def _clip01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _log_band(value: Optional[float], band: tuple[float, float]) -> float:
    """Map a value onto 0..1 across `band`, logarithmically."""
    lo, hi = band
    if not value or value <= lo:
        return 0.0
    return _clip01((math.log10(value) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)))


@dataclass
class ScoreBreakdown:
    """Every component that feeds the headline score, so it can be explained."""

    roi: float = 0.0
    profit: float = 0.0
    volume: float = 0.0
    stability: float = 0.0
    fill: float = 0.0
    freshness: float = 0.0
    total: float = 0.0
    notes: list[str] = field(default_factory=list)


# ROI alone rewards penny items -- a 1gp spread on a 2gp feather is a 50% return
# and a rounding error in coins. Absolute profit per cycle is weighted just as
# heavily so the ranking reflects gp actually earned per hour of attention.
WEIGHTS = {
    "roi": 0.24,
    "profit": 0.26,
    "volume": 0.18,
    "stability": 0.17,
    "fill": 0.08,
    "freshness": 0.07,
}

# Each component is normalised across a log band: at or below `lo` it scores 0,
# at or above `hi` it scores 1. Bands beat plain log scaling because log1p hands
# out generous partial credit at the bottom of the range -- 30k gp per cycle
# should read as "negligible", not as "60% of the way to excellent".
ROI_BAND = (0.005, 0.08)              # 0.5% .. 8% post-tax return
PROFIT_BAND = (10_000, 20_000_000)    # gp per 4 hour buy-limit cycle
VOLUME_BAND = (100, 100_000)          # units traded per day


def flip_score(
    *,
    roi_value: Optional[float],
    margin_value: Optional[int],
    vol_24h: Optional[int],
    margin_stability: Optional[float],
    est_fill_hours: Optional[float],
    data_age_seconds: Optional[int],
    potential_profit: Optional[int] = None,
) -> ScoreBreakdown:
    """Blend edge, size, liquidity and confidence into one 0-100 ranking number.

    The pieces are deliberately simple and bounded: a flip is worth doing when
    the edge is real (roi), the edge is worth real coins (profit), the item
    actually trades (volume), the edge has persisted (stability), a full buy
    limit can realistically be filled inside the 4 hour window (fill), and the
    quote is not stale (freshness). Every component is returned alongside the
    total so the ranking can always be explained rather than trusted blindly.
    """
    b = ScoreBreakdown()
    if not margin_value or margin_value <= 0 or not roi_value or roi_value <= 0:
        return b

    b.roi = _log_band(roi_value, ROI_BAND)
    b.profit = _log_band(potential_profit, PROFIT_BAND)
    b.volume = _log_band(vol_24h, VOLUME_BAND)
    b.stability = _clip01(margin_stability if margin_stability is not None else 0.4)
    if est_fill_hours is None:
        b.fill = 0.3
    else:
        # Filling a buy limit in well under 4 hours is ideal.
        b.fill = _clip01(1 - (est_fill_hours / 4.0))
    age = data_age_seconds if data_age_seconds is not None else 3600
    b.freshness = _clip01(1 - age / 1800)

    b.total = 100 * sum(WEIGHTS[k] * getattr(b, k) for k in WEIGHTS)

    if potential_profit is not None and potential_profit < 50_000:
        b.notes.append("small absolute profit per cycle")
    if (vol_24h or 0) < 500:
        b.notes.append("thin market: under 500 units traded in 24h")
    if margin_stability is not None and margin_stability < 0.4:
        b.notes.append("margin was negative for most of the last 24h")
    if age > 900:
        b.notes.append("quote is over 15 minutes old")
    return b


def est_fill_hours(buy_limit: Optional[int], hourly_volume: Optional[float]) -> Optional[float]:
    """Hours to buy a full 4h limit assuming you capture ~25% of one side's flow."""
    if not buy_limit or not hourly_volume or hourly_volume <= 0:
        return None
    capture = hourly_volume * 0.25
    if capture <= 0:
        return None
    return min(buy_limit / capture, 999.0)


def fillable_quantity(buy_limit: Optional[int], vol_24h: Optional[int]) -> int:
    """How many units it is realistic to move in one 4 hour cycle."""
    limit = buy_limit or 0
    if limit <= 0:
        # No published limit means untradeable in bulk here; fall back to flow.
        limit = int((vol_24h or 0) / 24)
    flow_cap = int((vol_24h or 0) / 6 * 0.25)  # a quarter of a 4h window's flow
    return max(0, min(limit, flow_cap) if flow_cap else limit)


def mean_or_none(values: Iterable[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return fmean(vals) if vals else None
