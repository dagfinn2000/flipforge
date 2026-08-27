"""Chart overlays and statistical helpers. Pure functions over sequences."""

from __future__ import annotations

import math
from statistics import fmean, pstdev
from typing import Optional, Sequence


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
    """Wilder's RSI. None until there are enough points to seed it."""
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
    out: list[Optional[float]] = []
    pv = vol = 0.0
    for p, v in zip(prices, volumes):
        if p is not None and v:
            pv += p * v
            vol += v
        out.append(pv / vol if vol else None)
    return out


def log_returns(values: Sequence[float]) -> list[float]:
    return [
        math.log(cur / prev)
        for prev, cur in zip(values, values[1:])
        if prev > 0 and cur > 0
    ]


def volatility(values: Sequence[float]) -> Optional[float]:
    """Standard deviation of log returns -- unitless and comparable across items."""
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


def coefficient_of_variation(values: Sequence[float]) -> Optional[float]:
    """Stdev relative to mean. The scale-free way to ask "how jumpy is this?"."""
    clean = [v for v in values if v is not None]
    if len(clean) < 3:
        return None
    mean = fmean(clean)
    if mean == 0:
        return None
    return pstdev(clean) / abs(mean)
