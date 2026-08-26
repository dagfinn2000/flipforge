"""Tests for the money math. If these pass, the numbers on screen are trustworthy."""

import math

import pytest

from app import analytics as a
from app.config import settings


class TestTax:
    def test_below_threshold_is_untaxed(self):
        assert a.sale_tax(99) == 0
        assert a.sale_tax(0) == 0
        assert a.sale_tax(None) == 0

    def test_standard_rate(self):
        assert a.sale_tax(100) == 2
        assert a.sale_tax(1000) == 20
        assert a.sale_tax(812_000) == 16_240

    def test_rounds_down_per_item(self):
        # 149 * 0.02 == 2.98 -> the player is charged 2, never 3.
        assert a.sale_tax(149) == 2

    def test_cap_applies_to_very_expensive_items(self):
        assert a.sale_tax(500_000_000) == settings.ge_tax_cap
        assert a.sale_tax(2_000_000_000) == settings.ge_tax_cap

    def test_exempt_items_pay_nothing(self):
        assert a.sale_tax(11_000_000, exempt=True) == 0
        assert a.is_tax_exempt("Old school bond")
        assert a.is_tax_exempt("  SPADE  ")
        assert not a.is_tax_exempt("Abyssal whip")


class TestMargin:
    def test_margin_is_net_of_tax(self):
        # Buy 792,763 / sell 799,576 looks like a 6,813 profit before tax and is
        # actually a loss once the 15,991 tax is taken.
        assert a.margin(792_763, 799_576) == -9_178

    def test_exempt_item_keeps_the_whole_spread(self):
        assert a.margin(11_250_000, 11_701_284, exempt=True) == 451_284

    def test_roi_matches_margin_over_capital(self):
        assert a.roi(100, 200) == pytest.approx(a.margin(100, 200) / 100)

    def test_missing_side_returns_none(self):
        assert a.margin(None, 500) is None
        assert a.roi(500, None) is None


class TestIndicators:
    def test_sma_warms_up_then_averages(self):
        out = a.sma([1, 2, 3, 4, 5], 3)
        assert out[:2] == [None, None]
        assert out[2:] == [2.0, 3.0, 4.0]

    def test_ema_tracks_a_rising_series(self):
        out = a.ema([10] * 5 + [20] * 15, 12)
        assert out[0] is None
        assert 10 < out[-1] <= 20

    def test_rsi_bounds(self):
        rising = a.rsi(list(range(1, 40)), 14)
        assert rising[-1] == pytest.approx(100.0)
        falling = a.rsi(list(range(40, 1, -1)), 14)
        assert falling[-1] == pytest.approx(0.0)
        assert all(v is None for v in a.rsi([1, 2, 3], 14))

    def test_bollinger_brackets_the_mean(self):
        values = [10, 12, 11, 13, 9, 14, 10, 12, 11, 13] * 3
        mid, up, low = a.bollinger(values, 20)
        i = len(values) - 1
        assert low[i] < mid[i] < up[i]

    def test_vwap_weights_by_volume(self):
        # A huge trade at 200 should drag the average far above the midpoint.
        assert a.vwap([100, 200], [1, 99])[-1] == pytest.approx(199.0)

    def test_volatility_is_zero_for_a_flat_series(self):
        assert a.volatility([100] * 10) == pytest.approx(0.0)
        assert a.volatility([100, 120, 90, 130]) > 0

    def test_zscore_needs_history(self):
        assert a.zscore(10, [1, 2]) is None
        assert a.zscore(10, [5, 5, 5, 5, 5]) == 0.0


class TestScoring:
    def _score(self, **kw):
        base = dict(
            roi_value=0.05, margin_value=1000, vol_24h=50_000,
            margin_stability=0.9, est_fill_hours=1.0,
            data_age_seconds=60, potential_profit=1_000_000,
        )
        base.update(kw)
        return a.flip_score(**base)

    def test_unprofitable_flips_score_zero(self):
        assert self._score(margin_value=-5).total == 0.0
        assert self._score(roi_value=-0.01).total == 0.0

    def test_score_is_bounded(self):
        top = self._score(roi_value=5, vol_24h=10**9, potential_profit=10**9,
                          margin_stability=1.0, est_fill_hours=0.0, data_age_seconds=0)
        assert top.total == pytest.approx(100.0)
        assert 0 <= self._score().total <= 100

    def test_penny_flip_ranks_below_a_real_one(self):
        """A 1gp spread on a 2gp item must not outrank a 5k margin on 12k volume."""
        feather = self._score(roi_value=0.5, vol_24h=16_000_000, potential_profit=30_000)
        real = self._score(roi_value=0.093, vol_24h=12_100, potential_profit=2_640_000)
        assert real.total > feather.total

    def test_stale_quotes_are_penalised(self):
        assert self._score(data_age_seconds=60).total > self._score(data_age_seconds=1800).total

    def test_weights_sum_to_one(self):
        assert sum(a.WEIGHTS.values()) == pytest.approx(1.0)

    def test_notes_flag_thin_markets(self):
        notes = self._score(vol_24h=100, potential_profit=1000).notes
        assert any("thin market" in n for n in notes)


class TestCapacity:
    def test_fill_time_scales_with_flow(self):
        fast = a.est_fill_hours(1000, 4000)
        slow = a.est_fill_hours(1000, 100)
        assert fast < slow
        assert a.est_fill_hours(1000, 0) is None
        assert a.est_fill_hours(None, 500) is None

    def test_fillable_quantity_respects_the_buy_limit(self):
        assert a.fillable_quantity(70, 10**9) == 70

    def test_fillable_quantity_respects_thin_flow(self):
        # 24h volume of 600 units means a 4h window only sees ~100.
        assert a.fillable_quantity(10_000, 600) < 100

    def test_pct_change(self):
        assert a.pct_change(100, 150) == pytest.approx(0.5)
        assert a.pct_change(0, 150) is None
        assert a.pct_change(None, 150) is None
