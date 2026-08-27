"""Slot allocator: spread a bankroll across the 8 Grand Exchange slots.

A ranked list tells you what is good. It does not tell you what to actually do
with 200m and eight slots, which is a different question -- the best single flip
is rarely the best use of all eight.

Formulated as a bounded knapsack with a cardinality (slot) constraint. One
structural property makes it tractable: for a chosen item, profit and capital
are both linear in quantity, so the return per coin is exactly its ROI and there
is never a reason to part-fund an item while a higher-ROI one is still short.
An optimal plan therefore funds every chosen item to its cap except at most one,
which absorbs whatever bankroll is left. That reduces the problem to choosing
which items get slots -- solved here by an ROI-ordered greedy seed followed by
local swap improvement, which is a heuristic and is described as one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class Candidate:
    item_id: int
    name: str
    price: int                 # instant-sell price, what you pay to buy in
    margin: int                # post-tax profit per unit
    max_quantity: int          # realistic fill in one 4h cycle
    score: float = 0.0
    volume_24h: int = 0
    buy_limit: Optional[int] = None
    est_fill_hours: Optional[float] = None
    icon_url: Optional[str] = None
    members: bool = False

    @property
    def roi(self) -> float:
        return self.margin / self.price if self.price > 0 else 0.0

    def capacity(self, per_item_cap: int) -> int:
        """Coins this item can usefully absorb, after the diversification cap."""
        return min(self.max_quantity * self.price, per_item_cap)


@dataclass
class Allocation:
    candidate: Candidate
    quantity: int
    capital: int
    profit: int
    pinned: bool = False

    def as_dict(self) -> dict:
        c = self.candidate
        return {
            "item_id": c.item_id, "name": c.name, "icon_url": c.icon_url,
            "price": c.price, "margin": c.margin, "roi": c.roi, "score": c.score,
            "buy_limit": c.buy_limit, "volume_24h": c.volume_24h,
            "est_fill_hours": c.est_fill_hours, "members": c.members,
            "quantity": self.quantity, "capital": self.capital,
            "profit": self.profit, "pinned": self.pinned,
        }


@dataclass
class Plan:
    allocations: list[Allocation] = field(default_factory=list)
    bankroll: int = 0
    slots: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def capital_used(self) -> int:
        return sum(a.capital for a in self.allocations)

    @property
    def expected_profit(self) -> int:
        return sum(a.profit for a in self.allocations)

    @property
    def slots_used(self) -> int:
        return len(self.allocations)

    def as_dict(self) -> dict:
        return {
            "allocations": [a.as_dict() for a in self.allocations],
            "bankroll": self.bankroll,
            "slots": self.slots,
            "slots_used": self.slots_used,
            "capital_used": self.capital_used,
            "capital_idle": max(self.bankroll - self.capital_used, 0),
            "expected_profit": self.expected_profit,
            "expected_return": (
                self.expected_profit / self.capital_used if self.capital_used else None
            ),
            "notes": self.notes,
        }


def _fund(
    chosen: Sequence[Candidate], bankroll: int, per_item_cap: int, pinned: set[int]
) -> list[Allocation]:
    """Fill the chosen items in ROI order until the bankroll runs out."""
    allocations: list[Allocation] = []
    remaining = bankroll
    for candidate in sorted(chosen, key=lambda c: c.roi, reverse=True):
        if remaining <= 0 or candidate.price <= 0:
            continue
        budget = min(candidate.capacity(per_item_cap), remaining)
        quantity = budget // candidate.price
        if quantity <= 0:
            continue
        capital = quantity * candidate.price
        allocations.append(
            Allocation(
                candidate=candidate, quantity=quantity, capital=capital,
                profit=quantity * candidate.margin,
                pinned=candidate.item_id in pinned,
            )
        )
        remaining -= capital
    return allocations


def _value(chosen: Sequence[Candidate], bankroll: int, per_item_cap: int) -> int:
    return sum(a.profit for a in _fund(chosen, bankroll, per_item_cap, set()))


def solve(
    candidates: Iterable[Candidate],
    *,
    bankroll: int,
    slots: int,
    max_share: float = 0.35,
    pinned_ids: Sequence[int] = (),
    search_width: int = 90,
    passes: int = 3,
) -> Plan:
    """Choose an allocation across `slots` maximising expected post-tax profit."""
    pool = [c for c in candidates if c.margin > 0 and c.price > 0 and c.max_quantity > 0]
    plan = Plan(bankroll=bankroll, slots=slots)
    if not pool or bankroll <= 0 or slots <= 0:
        plan.notes.append("no candidates matched the filters")
        return plan

    # The diversification cap must never bind tighter than an equal split would:
    # with one slot there is nothing to diversify across, and a 35% cap would
    # otherwise strand two thirds of the bankroll by construction.
    # The diversification cap must never bind tighter than an equal split would:
    # with one slot there is nothing to diversify across, and a 35% cap would
    # otherwise strand two thirds of the bankroll by construction.
    per_item_cap = max(int(bankroll * max_share), bankroll // slots, 1)
    pinned = set(pinned_ids)

    forced = [c for c in pool if c.item_id in pinned][:slots]
    optional = [c for c in pool if c.item_id not in pinned]

    # Build the search pool from three different rankings. Ranking by ROI alone
    # fills the shortlist with microcap items -- a 40% return on 20k of capacity
    # is a wonderful trade and no help at all to a 200m bankroll, and if no
    # large-capacity item is ever a candidate, no amount of local search can
    # find one.
    def contribution(c: Candidate) -> float:
        return c.capacity(per_item_cap) * c.roi

    per_ranking = max(search_width // 3, 10)
    shortlist: list[Candidate] = []
    seen: set[int] = set()
    for key in (lambda c: c.roi, contribution, lambda c: c.capacity(per_item_cap)):
        for candidate in sorted(optional, key=key, reverse=True)[:per_ranking]:
            if candidate.item_id not in seen:
                seen.add(candidate.item_id)
                shortlist.append(candidate)

    free_slots = max(slots - len(forced), 0)

    # Seed greedily on the real objective rather than on a proxy for it: add
    # whichever remaining item raises expected profit the most, given that the
    # bankroll is finite and the already-chosen items will be funded first.
    chosen = list(forced)
    available = list(shortlist)
    for _ in range(free_slots):
        best_gain, best_index = 0, None
        current = _value(chosen, bankroll, per_item_cap)
        for i, candidate in enumerate(available):
            gain = _value(chosen + [candidate], bankroll, per_item_cap) - current
            if gain > best_gain:
                best_gain, best_index = gain, i
        if best_index is None:
            break
        chosen.append(available.pop(best_index))

    # Local search: swap a chosen item for a benched one whenever it helps.
    best = _value(chosen, bankroll, per_item_cap)
    for _ in range(passes):
        best_swap = None
        for i, current_item in enumerate(chosen):
            if current_item.item_id in pinned:
                continue
            for j, alternative in enumerate(available):
                trial = list(chosen)
                trial[i] = alternative
                value = _value(trial, bankroll, per_item_cap)
                if value > best:
                    best, best_swap = value, (i, j)
        if best_swap is None:
            break
        i, j = best_swap
        chosen[i], available[j] = available[j], chosen[i]

    plan.allocations = _fund(chosen, bankroll, per_item_cap, pinned)
    plan.allocations.sort(key=lambda a: a.profit, reverse=True)

    idle = plan.bankroll - plan.capital_used
    if idle > bankroll * 0.2:
        plan.notes.append(
            f"{idle:,} gp stays idle: buy limits and the {max_share:.0%} "
            "per-item cap bound how much can be deployed this cycle"
        )
    if any(a.candidate.est_fill_hours and a.candidate.est_fill_hours > 4 for a in plan.allocations):
        plan.notes.append("some picks are unlikely to fill inside one 4 hour window")
    if len(plan.allocations) < slots:
        plan.notes.append(
            f"only {len(plan.allocations)} of {slots} slots could be filled from the "
            "candidates that passed your filters"
        )
    return plan
