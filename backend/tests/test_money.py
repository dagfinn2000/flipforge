"""Tests for the money module.

If these pass, every coin figure in the app is trustworthy. Includes
property-based tests, because the tax function is piecewise (floor, then a cap)
and piecewise functions are exactly where hand-picked examples miss things.
"""

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app import money
from app.money import Fill, Purchase, TaxPolicy

STANDARD = TaxPolicy()
EXEMPT = TaxPolicy(exempt_item_ids=frozenset({617}))
ONE_PERCENT = TaxPolicy(rate=Decimal("0.01"))
NO_TAX = TaxPolicy(rate=Decimal("0"))

prices = st.integers(min_value=0, max_value=2_147_483_647)
quantities = st.integers(min_value=1, max_value=100_000)


class TestThreshold:
    def test_derived_from_rate_not_hardcoded(self):
        # At 2% the first taxable price is 50, not the widely repeated 100.
        assert STANDARD.free_below == 50
        assert ONE_PERCENT.free_below == 100
        assert TaxPolicy(rate=Decimal("0.05")).free_below == 20

    def test_the_stale_100gp_figure_would_lose_tax(self):
        """A 75gp sale is taxed 1gp today. Treating it as free is a real error."""
        assert money.sale_tax(75, STANDARD) == 1
        assert money.sale_tax(49, STANDARD) == 0
        assert money.sale_tax(50, STANDARD) == 1


class TestSaleTax:
    def test_floors_per_item(self):
        assert money.sale_tax(149, STANDARD) == 2      # 2.98 floors to 2
        assert money.sale_tax(1000, STANDARD) == 20
        assert money.sale_tax(812_000, STANDARD) == 16_240

    def test_cap(self):
        assert money.sale_tax(250_000_000, STANDARD) == 5_000_000
        assert money.sale_tax(1_400_000_000, STANDARD) == 5_000_000

    def test_exempt_and_zero_rate(self):
        assert money.sale_tax(11_000_000, EXEMPT, item_id=617) == 0
        assert money.sale_tax(11_000_000, EXEMPT, item_id=4151) == 220_000
        assert money.sale_tax(1_000_000, NO_TAX) == 0

    def test_non_positive(self):
        assert money.sale_tax(0, STANDARD) == 0
        assert money.sale_tax(None, STANDARD) == 0

    @given(price=prices)
    def test_never_negative_and_never_over_cap(self, price):
        tax = money.sale_tax(price, STANDARD)
        assert 0 <= tax <= STANDARD.cap

    @given(price=prices)
    def test_never_exceeds_the_price_itself(self, price):
        assert money.sale_tax(price, STANDARD) <= max(price, 0)

    @given(price=prices)
    def test_monotonic_in_price(self, price):
        assert money.sale_tax(price, STANDARD) <= money.sale_tax(price + 1, STANDARD)


class TestNetReceived:
    def test_scales_with_quantity(self):
        assert money.net_received(1000, 5, STANDARD) == 5 * 980

    @given(price=prices, qty=quantities)
    def test_never_exceeds_gross(self, price, qty):
        assert money.net_received(price, qty, STANDARD) <= price * qty

    @given(price=prices, qty=quantities)
    def test_never_negative(self, price, qty):
        assert money.net_received(price, qty, STANDARD) >= 0


class TestMargin:
    def test_is_post_tax(self):
        # 6,813 raw spread is a 9,178 loss once tax lands.
        assert money.margin(792_763, 799_576, STANDARD) == -9_178

    def test_exempt_keeps_the_spread(self):
        assert money.margin(11_250_000, 11_701_284, EXEMPT, item_id=617) == 451_284

    def test_roi_is_exact(self):
        assert money.roi(100, 200, STANDARD) == Decimal(96) / Decimal(100)

    def test_missing_side(self):
        assert money.margin(None, 5, STANDARD) is None
        assert money.margin(5, None, STANDARD) is None
        assert money.roi(5, None, STANDARD) is None


class TestBreakeven:
    def test_is_above_the_buy_price(self):
        assert money.breakeven_sell(1_000_000, STANDARD) > 1_000_000

    def test_exempt_breaks_even_at_cost(self):
        assert money.breakeven_sell(1_000_000, EXEMPT, item_id=617) == 1_000_000

    def test_above_the_cap_it_is_buy_plus_cap(self):
        # Past 250m the tax stops growing, so buy / (1 - rate) is wrong here.
        buy = 1_000_000_000
        assert money.breakeven_sell(buy, STANDARD) == buy + 5_000_000

    @given(buy=st.integers(min_value=1, max_value=2_000_000_000))
    @settings(max_examples=300)
    def test_breakeven_always_covers_the_buy(self, buy):
        be = money.breakeven_sell(buy, STANDARD)
        assert money.net_received(be, 1, STANDARD) >= buy

    @given(buy=st.integers(min_value=1, max_value=2_000_000_000))
    @settings(max_examples=300)
    def test_breakeven_is_minimal(self, buy):
        be = money.breakeven_sell(buy, STANDARD)
        if be > buy:
            assert money.net_received(be - 1, 1, STANDARD) < buy

    @given(buy=st.integers(min_value=1, max_value=100_000_000))
    @settings(max_examples=200)
    def test_selling_at_breakeven_is_never_a_loss(self, buy):
        be = money.breakeven_sell(buy, STANDARD)
        assert money.margin(buy, be, STANDARD) >= 0


class TestCrossedQuotes:
    def test_detects_inversion(self):
        assert money.is_crossed(buy=810_000, sell=799_000)
        assert not money.is_crossed(buy=799_000, sell=810_000)
        assert not money.is_crossed(None, 500)

    def test_margin_stays_negative_rather_than_clamped(self):
        assert money.margin(810_000, 799_000, STANDARD) < 0


class TestLimitWindow:
    def test_counts_only_the_last_four_hours(self):
        now = 1_000_000
        purchases = [
            Purchase(30, now - 5 * 3600),   # aged out
            Purchase(20, now - 3600),
            Purchase(10, now - 60),
        ]
        w = money.limit_window(70, purchases, now)
        assert w.used == 30
        assert w.remaining == 40

    def test_reset_time_follows_the_oldest_live_purchase(self):
        now = 1_000_000
        w = money.limit_window(70, [Purchase(20, now - 3600)], now)
        assert w.resets_at == now - 3600 + 4 * 3600

    def test_never_negative_when_over_limit(self):
        now = 1_000_000
        w = money.limit_window(10, [Purchase(50, now)], now)
        assert w.remaining == 0

    def test_unlimited_item(self):
        w = money.limit_window(None, [Purchase(5, 10)], 20)
        assert w.remaining is None and w.used == 5


class TestFifo:
    def test_realised_uses_actual_cost_basis(self):
        result = money.match_fifo(
            [Fill("buy", 10, 790_000), Fill("sell", 4, 830_000)], STANDARD
        )
        # tax 16,600/unit -> (830000-16600-790000) * 4
        assert result.realised == 93_600
        assert result.tax_paid == 66_400
        assert result.open_quantity == 6

    def test_oldest_lot_goes_first(self):
        result = money.match_fifo(
            [Fill("buy", 5, 100), Fill("buy", 5, 200), Fill("sell", 5, 300)], NO_TAX
        )
        assert result.realised == (300 - 100) * 5
        assert result.open_lots[0].price == 200

    def test_unmatched_sales_are_flagged_not_dropped(self):
        result = money.match_fifo([Fill("sell", 7, 500)], NO_TAX)
        assert result.unmatched_sales == 7
        assert result.realised == 3500

    def test_unrealised_is_post_tax(self):
        result = money.match_fifo([Fill("buy", 6, 790_000)], STANDARD)
        assert money.unrealised(result, 799_576, STANDARD) == -38_490

    @given(
        fills=st.lists(
            st.tuples(
                st.sampled_from(["buy", "sell"]),
                st.integers(min_value=1, max_value=500),
                st.integers(min_value=1, max_value=1_000_000),
            ),
            max_size=40,
        )
    )
    @settings(max_examples=300)
    def test_quantity_is_conserved(self, fills):
        """Everything bought is either still open or was matched to a sale."""
        result = money.match_fifo([Fill(s, q, p) for s, q, p in fills], STANDARD)
        matched = result.sold - result.unmatched_sales
        assert result.open_quantity == result.bought - matched
        assert result.open_quantity >= 0
        assert 0 <= matched <= result.bought

    @given(
        fills=st.lists(
            st.tuples(
                st.sampled_from(["buy", "sell"]),
                st.integers(min_value=1, max_value=200),
                st.integers(min_value=1, max_value=100_000),
            ),
            max_size=30,
        )
    )
    @settings(max_examples=200)
    def test_tax_paid_never_negative(self, fills):
        result = money.match_fifo([Fill(s, q, p) for s, q, p in fills], STANDARD)
        assert result.tax_paid >= 0
        assert result.cost_basis >= 0
