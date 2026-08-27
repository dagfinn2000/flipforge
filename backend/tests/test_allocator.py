"""Tests for the slot allocator."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.allocator import Candidate, solve


def cand(item_id, price, margin, max_quantity, **kw):
    return Candidate(
        item_id=item_id, name=f"item{item_id}", price=price,
        margin=margin, max_quantity=max_quantity, **kw
    )


class TestConstraints:
    def test_never_exceeds_bankroll(self):
        pool = [cand(i, 1000, 100, 10_000) for i in range(20)]
        plan = solve(pool, bankroll=1_000_000, slots=8)
        assert plan.capital_used <= 1_000_000

    def test_never_exceeds_slot_count(self):
        pool = [cand(i, 100, 10, 100_000) for i in range(50)]
        plan = solve(pool, bankroll=10_000_000, slots=3)
        assert plan.slots_used <= 3

    def test_respects_buy_limit_quantity(self):
        plan = solve([cand(1, 1000, 500, 7)], bankroll=10_000_000, slots=8)
        assert plan.allocations[0].quantity == 7

    def test_diversification_cap_forces_spread(self):
        """One huge-capacity item must not absorb the whole bankroll."""
        pool = [cand(1, 100, 50, 10_000_000)] + [cand(i, 100, 10, 1_000_000) for i in range(2, 10)]
        plan = solve(pool, bankroll=1_000_000, slots=8, max_share=0.25)
        top = max(plan.allocations, key=lambda a: a.capital)
        assert top.capital <= 250_000
        assert plan.slots_used > 1

    def test_unprofitable_items_are_never_allocated(self):
        plan = solve([cand(1, 1000, -50, 1000), cand(2, 1000, 0, 1000)], bankroll=10**6, slots=8)
        assert plan.allocations == []

    def test_empty_pool_is_explained_not_crashed(self):
        plan = solve([], bankroll=10**6, slots=8)
        assert plan.allocations == [] and plan.notes


class TestQuality:
    def test_prefers_higher_roi_when_capacity_is_equal(self):
        pool = [cand(1, 1000, 50, 1000), cand(2, 1000, 200, 1000)]
        plan = solve(pool, bankroll=500_000, slots=1)
        assert plan.allocations[0].candidate.item_id == 2

    def test_swaps_away_from_a_tiny_high_roi_item(self):
        """The greedy trap: 50% ROI on 2gp of capacity should lose a scarce slot
        to a 10% ROI item that can absorb the whole bankroll."""
        tiny = cand(1, 2, 1, 1)                      # 50% ROI, 2gp capacity
        big = cand(2, 1000, 100, 10_000)             # 10% ROI, 10m capacity
        plan = solve([tiny, big], bankroll=1_000_000, slots=1)
        assert plan.allocations[0].candidate.item_id == 2
        assert plan.expected_profit == 100_000

    def test_uses_extra_slots_to_deploy_idle_capital(self):
        pool = [cand(i, 1000, 100, 100) for i in range(1, 9)]   # 100k capacity each
        one = solve(pool, bankroll=800_000, slots=1)
        eight = solve(pool, bankroll=800_000, slots=8)
        assert eight.expected_profit > one.expected_profit

    def test_pinned_items_are_always_included(self):
        pool = [cand(1, 1000, 1, 10)] + [cand(i, 1000, 500, 10_000) for i in range(2, 20)]
        plan = solve(pool, bankroll=10_000_000, slots=3, pinned_ids=[1])
        assert 1 in [a.candidate.item_id for a in plan.allocations]
        assert any(a.pinned for a in plan.allocations)

    def test_profit_matches_quantity_times_margin(self):
        plan = solve([cand(1, 1000, 250, 40)], bankroll=10**9, slots=8)
        a = plan.allocations[0]
        assert a.profit == a.quantity * 250
        assert a.capital == a.quantity * 1000


class TestProperties:
    @given(
        pool=st.lists(
            st.tuples(
                st.integers(min_value=1, max_value=1_000_000),   # price
                st.integers(min_value=1, max_value=50_000),      # margin
                st.integers(min_value=1, max_value=10_000),      # max qty
            ),
            min_size=1, max_size=25,
        ),
        bankroll=st.integers(min_value=1_000, max_value=5_000_000_000),
        slots=st.integers(min_value=1, max_value=8),
    )
    @settings(max_examples=200, deadline=None)
    def test_invariants_hold_for_any_pool(self, pool, bankroll, slots):
        candidates = [cand(i, p, m, q) for i, (p, m, q) in enumerate(pool)]
        plan = solve(candidates, bankroll=bankroll, slots=slots)

        assert plan.capital_used <= bankroll
        assert plan.slots_used <= slots
        assert plan.expected_profit >= 0
        seen = [a.candidate.item_id for a in plan.allocations]
        assert len(seen) == len(set(seen)), "an item must not occupy two slots"
        for a in plan.allocations:
            assert 0 < a.quantity <= a.candidate.max_quantity
            assert a.capital == a.quantity * a.candidate.price
            assert a.profit == a.quantity * a.candidate.margin
