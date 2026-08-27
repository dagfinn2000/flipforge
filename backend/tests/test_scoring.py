"""Tests for the flip score. Bounded components, auditable contributions."""

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app import scoring
from app.scoring import WEIGHTS, flip_score


def score(**kw):
    base = dict(
        roi=Decimal("0.05"), margin=1000, potential_profit=1_000_000,
        volume_24h=50_000, margin_cv=0.3, est_fill_hours=1.0, quote_age_seconds=60,
    )
    base.update(kw)
    return flip_score(**base)


class TestModel:
    def test_weights_sum_to_one(self):
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_has_the_six_specified_components(self):
        keys = {c.key for c in score().components}
        assert keys == {"roi", "profit", "liquidity", "stability", "fill", "freshness"}

    def test_contributions_sum_to_the_total(self):
        s = score()
        assert sum(c.contribution for c in s.components) == pytest.approx(s.total)

    def test_every_component_is_bounded(self):
        s = score(roi=Decimal("50"), potential_profit=10**12, volume_24h=10**9)
        assert all(0 <= c.value <= 1 for c in s.components)
        assert s.total <= 100


class TestBehaviour:
    def test_unprofitable_scores_zero_with_a_reason(self):
        s = score(margin=-1)
        assert s.total == 0.0
        assert any("no post-tax edge" in n for n in s.notes)

    def test_penny_flip_loses_to_a_real_one(self):
        """The classic ROI trap: 50% on a 1gp feather spread must not win."""
        feather = score(roi=Decimal("0.5"), potential_profit=30_000, volume_24h=16_000_000)
        real = score(roi=Decimal("0.093"), potential_profit=2_640_000, volume_24h=12_100)
        assert real.total > feather.total

    def test_a_flickering_margin_scores_below_a_steady_one(self):
        steady = score(margin_cv=0.2)
        flicker = score(margin_cv=3.0)
        assert steady.total > flicker.total
        assert any("flicker" in n for n in flicker.notes)

    def test_unknown_stability_is_not_treated_as_good(self):
        assert score(margin_cv=None).total < score(margin_cv=0.1).total

    def test_stale_quotes_are_penalised(self):
        assert score(quote_age_seconds=30).total > score(quote_age_seconds=1700).total

    def test_slow_fills_are_penalised(self):
        assert score(est_fill_hours=0.5).total > score(est_fill_hours=3.9).total

    def test_beyond_the_window_fill_scores_nothing(self):
        assert score(est_fill_hours=10).component("fill").value == 0.0


class TestCapacity:
    def test_fill_time_scales_with_flow(self):
        assert scoring.est_fill_hours(1000, 4000) < scoring.est_fill_hours(1000, 100)
        assert scoring.est_fill_hours(1000, 0) is None

    def test_fillable_respects_buy_limit(self):
        assert scoring.fillable_quantity(70, 10**9) == 70

    def test_fillable_respects_thin_flow(self):
        assert scoring.fillable_quantity(10_000, 600) < 100


class TestProperties:
    @given(
        roi=st.floats(min_value=0.0001, max_value=50, allow_nan=False),
        profit=st.integers(min_value=0, max_value=10**11),
        volume=st.integers(min_value=0, max_value=10**9),
        cv=st.floats(min_value=0, max_value=100, allow_nan=False),
        fill=st.floats(min_value=0, max_value=500, allow_nan=False),
        age=st.integers(min_value=0, max_value=10**6),
    )
    @settings(max_examples=400)
    def test_score_is_always_in_range(self, roi, profit, volume, cv, fill, age):
        s = flip_score(
            roi=roi, margin=1000, potential_profit=profit, volume_24h=volume,
            margin_cv=cv, est_fill_hours=fill, quote_age_seconds=age,
        )
        assert 0.0 <= s.total <= 100.0
        for c in s.components:
            assert 0.0 <= c.value <= 1.0
            assert c.contribution >= 0

    @given(
        a=st.integers(min_value=1_000, max_value=10**9),
        b=st.integers(min_value=1_000, max_value=10**9),
    )
    @settings(max_examples=200)
    def test_more_profit_never_scores_lower(self, a, b):
        """Every component is monotone in the right direction by construction."""
        lo, hi = min(a, b), max(a, b)
        assert score(potential_profit=lo).total <= score(potential_profit=hi).total


class TestSilentMarkets:
    """An item nobody traded is not an opportunity, however good its spread looks."""

    def test_nothing_is_fillable_without_volume(self):
        assert scoring.fillable_quantity(70, 0) == 0
        assert scoring.fillable_quantity(70, None) == 0
        assert scoring.fillable_quantity(30_000, 0) == 0

    def test_buy_limit_is_a_ceiling_not_evidence(self):
        """The regression: a silent market once advertised billions per cycle
        because the buy limit was used as the quantity when volume was zero."""
        assert scoring.fillable_quantity(30_000, 0) == 0

    def test_volume_too_thin_to_fill_anything_rounds_to_zero(self):
        # 20 units a day is ~3 in a 4h window; a quarter of that is not one unit.
        assert scoring.fillable_quantity(1000, 20) == 0

    def test_score_is_zero_without_volume(self):
        s = flip_score(
            roi=Decimal("0.3"), margin=50_000, potential_profit=2_000_000_000,
            volume_24h=0, margin_cv=0.1, est_fill_hours=0.1, quote_age_seconds=0,
        )
        assert s.total == 0.0
        assert any("no trades" in n for n in s.notes)
        assert all(c.value == 0.0 for c in s.components)

    def test_a_single_traded_unit_still_scores(self):
        s = flip_score(
            roi=Decimal("0.05"), margin=1000, potential_profit=1000,
            volume_24h=5000, margin_cv=0.3, est_fill_hours=1.0, quote_age_seconds=60,
        )
        assert s.total > 0


class TestTrackScore:
    """The measured counterpart: how flips actually turned out, not how they look."""

    def _track(self, **kw):
        base = dict(samples=60, win_rate=0.85, median_cycle_profit=500_000)
        base.update(kw)
        return scoring.track_score(**base)

    def test_weights_sum_to_one(self):
        assert sum(scoring.TRACK_WEIGHTS.values()) == pytest.approx(1.0)

    def test_no_evidence_scores_zero(self):
        s = scoring.track_score(samples=0, win_rate=None, median_cycle_profit=None)
        assert s.total == 0.0
        assert any("no graded flips" in n for n in s.notes)

    def test_a_coin_flip_earns_nothing_on_the_win_term(self):
        assert self._track(win_rate=0.5).component_value("win_rate") == 0.0
        assert self._track(win_rate=0.3).component_value("win_rate") == 0.0

    def test_thin_evidence_is_discounted_not_trusted(self):
        """A perfect record over 3 flips must not outrank a strong one over 60."""
        thin = self._track(samples=3, win_rate=1.0, median_cycle_profit=5_000_000)
        solid = self._track(samples=60, win_rate=0.85, median_cycle_profit=500_000)
        assert thin.confidence < solid.confidence
        assert solid.total > thin.total
        assert any("discounted" in n for n in thin.notes)

    def test_losing_items_are_flagged(self):
        s = self._track(win_rate=0.2, median_cycle_profit=-1000)
        assert s.total == 0.0
        assert any("lost more often" in n for n in s.notes)
        assert any("flat or down" in n for n in s.notes)

    def test_more_profit_scores_higher(self):
        assert self._track(median_cycle_profit=5_000_000).total > \
               self._track(median_cycle_profit=50_000).total

    def test_bounded(self):
        top = self._track(samples=10_000, win_rate=1.0, median_cycle_profit=10**10)
        assert 0 <= top.total <= 100
