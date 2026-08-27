"""Grand Exchange money maths.

The single source of truth for every coin figure the app reports. Pure: no I/O,
no globals beyond an injected TaxPolicy, so it is fully testable and every
downstream number (margin, ROI, score, portfolio P&L) follows from here.

Money is handled as int gp or Decimal. Never float -- binary floats cannot
represent 0.02 exactly, and a rounding error in a tax calculation is a lie about
someone's profit.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
from typing import Iterable, Optional, Sequence

# --------------------------------------------------------------------- policy --

DEFAULT_RATE = Decimal("0.02")      # 2% since 29 May 2025; was 1% before that
DEFAULT_CAP = 5_000_000             # per item, so a 1.4b bow pays 5m, not 28m


@dataclass(frozen=True)
class TaxPolicy:
    """The tax rules in force. Loaded from config and a seeded table, never
    hardcoded in business logic -- Jagex has already changed the rate once."""

    rate: Decimal = DEFAULT_RATE
    cap: int = DEFAULT_CAP
    exempt_item_ids: frozenset[int] = frozenset()

    @property
    def free_below(self) -> int:
        """Lowest price at which any tax is actually charged.

        Derived, never hardcoded: tax floors per item, so the threshold is
        wherever `floor(price * rate)` first reaches 1. At 2% that is 50gp. The
        widely repeated "under 100gp is free" is a leftover from the 1% era and
        is wrong today -- it silently under-reports tax on everything from 50 to
        99gp.
        """
        if self.rate <= 0:
            return 0
        # Smallest integer p with p * rate >= 1.
        return int(-(-1 // self.rate)) if self.rate else 0

    def is_exempt(self, item_id: Optional[int]) -> bool:
        return item_id is not None and item_id in self.exempt_item_ids


# ------------------------------------------------------------------------ tax --

def sale_tax(price: Optional[int], policy: TaxPolicy, item_id: Optional[int] = None) -> int:
    """Tax taken from the seller for ONE item sold at `price`.

    Floors per individual item (not per offer) and is capped per item.
    """
    if price is None or price <= 0 or policy.is_exempt(item_id):
        return 0
    raw = (Decimal(price) * policy.rate).to_integral_value(rounding=ROUND_FLOOR)
    return min(int(raw), policy.cap)


def net_received(
    price: Optional[int], quantity: int, policy: TaxPolicy, item_id: Optional[int] = None
) -> int:
    """Coins actually banked for selling `quantity` at `price` each."""
    if price is None or quantity <= 0:
        return 0
    return quantity * (price - sale_tax(price, policy, item_id))


def margin(
    buy: Optional[int], sell: Optional[int], policy: TaxPolicy, item_id: Optional[int] = None
) -> Optional[int]:
    """Post-tax profit per unit. There is no pre-tax variant on purpose."""
    if not buy or not sell:
        return None
    return net_received(sell, 1, policy, item_id) - buy


def roi(
    buy: Optional[int], sell: Optional[int], policy: TaxPolicy, item_id: Optional[int] = None
) -> Optional[Decimal]:
    m = margin(buy, sell, policy, item_id)
    if m is None or not buy:
        return None
    return Decimal(m) / Decimal(buy)


def breakeven_sell(buy: Optional[int], policy: TaxPolicy, item_id: Optional[int] = None) -> Optional[int]:
    """Lowest sell price whose post-tax proceeds still cover `buy`.

    Emphatically not equal to the buy price, which is the single most common
    flipping mistake: buy at 1,000,000 and selling at 1,000,000 loses 20,000.
    Solved by search rather than algebra because the tax cap makes the function
    piecewise, so `buy / (1 - rate)` is wrong for expensive items.
    """
    if not buy or buy <= 0:
        return None
    if policy.is_exempt(item_id) or policy.rate <= 0:
        return buy

    # Above the cap the tax stops growing, so the answer is simply buy + cap.
    if buy > policy.cap / policy.rate:
        return buy + policy.cap

    # Start from the algebraic estimate and step up until it truly covers.
    guess = int(Decimal(buy) / (Decimal(1) - policy.rate))
    candidate = max(guess - 2, buy)
    while net_received(candidate, 1, policy, item_id) < buy:
        candidate += 1
    # Walk back down in case the estimate overshot.
    while candidate > buy and net_received(candidate - 1, 1, policy, item_id) >= buy:
        candidate -= 1
    return candidate


def is_crossed(buy: Optional[int], sell: Optional[int]) -> bool:
    """True when instant-sell sits above instant-buy.

    A real condition in the feed, not corrupt data: the last two trades simply
    landed in an odd order. Surfaced rather than clamped, because clamping would
    invent a zero margin where a negative one is the truth.
    """
    return bool(buy and sell and buy > sell)


# ----------------------------------------------------------------- buy limits --

BUY_LIMIT_WINDOW_SECONDS = 4 * 3600


@dataclass(frozen=True)
class Purchase:
    """One buy, for rolling-window limit accounting."""

    quantity: int
    at_epoch: int


@dataclass(frozen=True)
class LimitWindow:
    """State of an item's 4 hour rolling buy allowance."""

    limit: Optional[int]
    used: int
    remaining: Optional[int]
    resets_at: Optional[int]      # when the oldest counted purchase ages out
    window_seconds: int = BUY_LIMIT_WINDOW_SECONDS


def limit_window(
    limit: Optional[int], purchases: Sequence[Purchase], now_epoch: int
) -> LimitWindow:
    """How much of the buy limit is still available right now.

    The limit is a rolling window, not a bucket that empties on the hour: each
    purchase frees its own quantity exactly four hours after it happened.
    """
    cutoff = now_epoch - BUY_LIMIT_WINDOW_SECONDS
    live = [p for p in purchases if p.at_epoch > cutoff]
    used = sum(p.quantity for p in live)
    if limit is None:
        return LimitWindow(limit=None, used=used, remaining=None, resets_at=None)
    oldest = min((p.at_epoch for p in live), default=None)
    return LimitWindow(
        limit=limit,
        used=used,
        remaining=max(limit - used, 0),
        resets_at=(oldest + BUY_LIMIT_WINDOW_SECONDS) if oldest is not None else None,
    )


# ------------------------------------------------------------------ portfolio --

@dataclass
class Lot:
    quantity: int
    price: int


@dataclass
class MatchResult:
    """Outcome of matching a ledger of trades FIFO."""

    realised: int = 0
    tax_paid: int = 0
    open_lots: list[Lot] = field(default_factory=list)
    bought: int = 0
    sold: int = 0
    unmatched_sales: int = 0

    @property
    def open_quantity(self) -> int:
        return sum(lot.quantity for lot in self.open_lots)

    @property
    def cost_basis(self) -> int:
        return sum(lot.quantity * lot.price for lot in self.open_lots)

    @property
    def average_cost(self) -> Optional[Decimal]:
        qty = self.open_quantity
        return Decimal(self.cost_basis) / Decimal(qty) if qty else None


@dataclass(frozen=True)
class Fill:
    side: str        # "buy" | "sell"
    quantity: int
    price: int
    at_epoch: int = 0


def match_fifo(
    fills: Iterable[Fill], policy: TaxPolicy, item_id: Optional[int] = None
) -> MatchResult:
    """Match sells against the oldest open buys.

    Quantity is conserved: everything bought either sits in an open lot or has
    been matched against a sale. Selling more than was ever bought is recorded
    as an unmatched sale rather than silently dropped, so a partial ledger still
    reconciles.
    """
    result = MatchResult()
    lots: deque[Lot] = deque()

    for fill in fills:
        if fill.quantity <= 0:
            continue
        if fill.side == "buy":
            lots.append(Lot(fill.quantity, fill.price))
            result.bought += fill.quantity
            continue

        result.sold += fill.quantity
        unit_net = fill.price - sale_tax(fill.price, policy, item_id)
        result.tax_paid += sale_tax(fill.price, policy, item_id) * fill.quantity
        remaining = fill.quantity
        while remaining > 0 and lots:
            lot = lots[0]
            take = min(remaining, lot.quantity)
            result.realised += (unit_net - lot.price) * take
            lot.quantity -= take
            remaining -= take
            if lot.quantity == 0:
                lots.popleft()
        if remaining > 0:
            # No cost basis on record; count the proceeds and flag the gap.
            result.realised += unit_net * remaining
            result.unmatched_sales += remaining

    result.open_lots = list(lots)
    return result


def unrealised(
    result: MatchResult, mark: Optional[int], policy: TaxPolicy, item_id: Optional[int] = None
) -> Optional[int]:
    """Post-tax gain if every open lot were sold at `mark` right now."""
    qty = result.open_quantity
    if not qty or not mark:
        return None
    return net_received(mark, qty, policy, item_id) - result.cost_basis
