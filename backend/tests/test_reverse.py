"""Reverse-DCF tests. The core property is self-consistency: solving for the
price the model itself produces must recover the model's own default."""

from __future__ import annotations

import pytest
from test_engine import VD, toy_history, toy_market, toy_model

from engine.reverse import FIELDS
from engine.reverse import implied_assumption as _implied_assumption


def implied_assumption(*args, **kw):
    # profile=None: like toy_model, these tests pin base defaults to verify
    # solver MECHANICS — the toy's flat revenues would classify declining
    kw.setdefault("profile", None)
    return _implied_assumption(*args, **kw)

BASE = toy_model()
BASE_PRICE = BASE.bridges["gordon"].value_per_share


class TestSelfConsistency:
    @pytest.mark.parametrize("field", FIELDS)
    def test_solving_for_own_price_recovers_the_default(self, field):
        r = implied_assumption(toy_history(), toy_market(), field, VD,
                               target_price=BASE_PRICE)
        assert r.status == "solved"
        assert r.implied == pytest.approx(r.derived, abs=1e-5), field

    def test_default_target_is_the_market_price(self):
        r = implied_assumption(toy_history(), toy_market(), "terminal_growth", VD)
        assert r.target_price == 50.0


class TestKnownTargets:
    def test_capex_solve_recovers_a_known_override(self):
        # price the model at capex 12%, then ask what capex the price implies
        target = toy_model(overrides={"capex_pct": 0.12}
                           ).bridges["gordon"].value_per_share
        r = implied_assumption(toy_history(), toy_market(), "capex_pct", VD,
                               target_price=target)
        assert r.implied == pytest.approx(0.12, abs=1e-5)

    def test_growth_solve_recovers_a_known_override(self):
        target = toy_model(overrides={"revenue_growth_fy1": 0.10}
                           ).bridges["gordon"].value_per_share
        r = implied_assumption(toy_history(), toy_market(), "revenue_growth_fy1",
                               VD, target_price=target)
        assert r.implied == pytest.approx(0.10, abs=1e-4)

    def test_implied_direction_reads_correctly(self):
        # a price ABOVE the model's output needs cheaper capex / higher g —
        # the direction is the whole point of the comparison. Capex gets a
        # modest premium: its reach is bounded to the explicit window
        # (terminal reinvestment is governed by g/ROIC, not year-5 capex).
        g = implied_assumption(toy_history(), toy_market(), "terminal_growth",
                               VD, target_price=BASE_PRICE * 1.5)
        capex = implied_assumption(toy_history(), toy_market(), "capex_pct",
                                   VD, target_price=BASE_PRICE * 1.05)
        assert g.implied > g.derived
        assert capex.implied < capex.derived

    def test_capex_reach_is_bounded_by_the_explicit_window(self):
        # a 50% premium is beyond what zeroing five years of capex can add —
        # the honest answer is no-solution, not an extreme number
        r = implied_assumption(toy_history(), toy_market(), "capex_pct",
                               VD, target_price=BASE_PRICE * 1.5)
        assert r.status == "no_solution_in_range"


class TestNoSolution:
    def test_terminal_growth_at_or_above_wacc_is_named_not_numbered(self):
        # as g → WACC the Gordon TV diverges, so most premiums DO solve below
        # WACC; 50× the model's own price does not — and must be named, not
        # numbered
        r = implied_assumption(toy_history(), toy_market(), "terminal_growth",
                               VD, target_price=BASE_PRICE * 50)
        assert r.status == "no_solution_below_wacc"
        assert r.implied is None

    def test_absurd_price_reports_no_solution_in_range(self):
        r = implied_assumption(toy_history(), toy_market(), "capex_pct",
                               VD, target_price=BASE_PRICE * 50)
        assert r.status == "no_solution_in_range"
        assert r.implied is None

    def test_unknown_field_rejected(self):
        with pytest.raises(ValueError):
            implied_assumption(toy_history(), toy_market(), "wacc", VD)


class TestEbitdaMarginLever:
    def test_solve_moves_margin_and_reports_fy1_basis(self):
        r = implied_assumption(toy_history(), toy_market(), "ebitda_margin", VD,
                               target_price=BASE_PRICE * 1.2)
        # toy FY1: EBIT margin 25% + D&A 80/1000 = 33% derived EBITDA margin
        assert r.derived == pytest.approx(0.33)
        assert r.status == "solved"
        assert r.implied > r.derived
