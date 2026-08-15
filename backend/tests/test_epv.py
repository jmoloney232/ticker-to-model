"""EPV + the valuation-methods registry (owner-approved 2026-08-15).

The test that matters most here: a DCF with zero growth in every forecast
year, zero terminal growth, capex equal to D&A, and flat working capital
MUST converge to the EPV value — the strongest available proof that the two
methods are consistent implementations of the same underlying economics.
Plus: per-profile margin normalization (methodology parity), the
declining-beats-cyclical collision, honest unavailable states, the
value-destructive inversion, and the fourth-method contract (serializer
stays generic; the workbook fails loudly for an id with no formula block).
"""

from pathlib import Path

import pytest
import yaml
from test_engine import VD, toy_history, toy_market
from test_profile import fixture_assumptions

from app.serialize import growth_out, method_out, serialize_model
from engine.assumptions import EPV_MARGIN_RULES, derive_assumptions
from engine.dcf import build_model
from engine.models import MethodAvailability, MethodResult

METHODOLOGY = Path(__file__).parent.parent / "engine" / "methodology.yaml"


def _mean(xs):
    return sum(xs) / len(xs)


def _margin(p):
    return p.value("operating_income") / p.value("revenue")


# ── the convergence property ─────────────────────────────────────────────────

def _flat_world_overrides(h):
    """Pin every ratio the projection consumes to its FY0-implied value so a
    zero-growth model reproduces FY0 exactly, forever: cost ratios (flat
    margin), WC ratios (ΔWC = 0), capex = D&A (flat PP&E, flat D&A), tax
    pinned at marginal."""
    fy0 = h.periods[-1]
    rev0 = fy0.value("revenue")
    cogs0 = fy0.value("cost_of_revenue")
    a = derive_assumptions(h, toy_market(), profile=None)
    named = (cogs0 + fy0.value("research_and_development", 0.0)
             + fy0.value("selling_general_admin", 0.0)
             + fy0.value("other_operating", 0.0))
    return {
        "revenue_growth_fy1": 0.0,
        "terminal_growth": 0.0,
        "effective_tax_fy1": a.eff("marginal_tax"),
        "cogs_pct": cogs0 / rev0,
        "rnd_pct": fy0.value("research_and_development", 0.0) / rev0,
        "sga_pct": fy0.value("selling_general_admin", 0.0) / rev0,
        "other_opex_pct": fy0.value("other_operating", 0.0) / rev0,
        "unclassified_costs_pct":
            (rev0 - fy0.value("operating_income") - named) / rev0,
        "capex_pct": (a.eff("da_pct_beginning_ppe")
                      * fy0.value("ppe_net") / rev0),
        "dso": 365 * fy0.value("accounts_receivable", 0.0) / rev0,
        "dio": 365 * fy0.value("inventory", 0.0) / cogs0,
        "dpo": 365 * fy0.value("accounts_payable", 0.0) / cogs0,
        "oca_pct": fy0.value("other_current_assets", 0.0) / rev0,
        "accrued_pct": fy0.value("accrued_liabilities", 0.0) / rev0,
        "ocl_pct": fy0.value("other_current_liabilities", 0.0) / rev0,
        "defrev_pct": fy0.value("deferred_revenue_current", 0.0) / rev0,
    }


class TestZeroGrowthConvergence:
    @pytest.mark.parametrize("midyear", [True, False])
    def test_zero_growth_dcf_converges_to_epv(self, midyear):
        h = toy_history()
        over = _flat_world_overrides(h)
        over["midyear"] = midyear
        m1 = build_model(h, toy_market(), valuation_date=VD,
                         overrides=over, profile=None)
        p1 = m1.projections[0]
        margin = p1.income["operating_income"] / p1.income["revenue"]
        m = build_model(h, toy_market(), valuation_date=VD,
                        overrides={**over, "epv_margin": margin},
                        profile=None)

        # the premise: every explicit year IS the no-growth steady state
        for y in m.ufcf:
            assert y.ufcf == pytest.approx(y.nopat, rel=1e-9)
            assert y.nopat == pytest.approx(m.ufcf[0].nopat, rel=1e-9)
        rr = m.terminal["gordon"].detail["reinvestment_rate"]
        assert rr == 0.0                       # RR = g/ROIC = 0 at g = 0

        # the theorem: explicit years + Gordon TV telescope to the perpetuity
        assert m.bridges["epv"].enterprise_value == pytest.approx(
            m.bridges["gordon"].enterprise_value, rel=1e-9)
        assert m.bridges["epv"].value_per_share == pytest.approx(
            m.bridges["gordon"].value_per_share, rel=1e-9)
        assert m.growth.per_share == pytest.approx(0.0, abs=1e-6)


# ── per-profile normalization rules ──────────────────────────────────────────

class TestMarginNormalization:
    def test_rules_match_methodology(self):
        doc = yaml.safe_load(METHODOLOGY.read_text())
        entry = next(c for c in doc["conventions"]
                     if c["id"] == "epv_margin_normalization")
        assert entry["rules"] == EPV_MARGIN_RULES

    def test_house_rule_is_trailing_3y_mean(self):
        # profile=None → 3y mean; compounder (MSFT) keeps the same rule
        h, _, a = fixture_assumptions("MSFT", profile=None)
        w = h.periods[-3:]
        assert a.fields["epv_margin"].value == pytest.approx(
            _mean([_margin(p) for p in w]))
        h, _, a_auto = fixture_assumptions("MSFT")          # compounder
        assert a_auto.fields["epv_margin"].value == pytest.approx(
            a.fields["epv_margin"].value)
        # compounder keeps the house rule → the default is NOT profile-tagged
        assert a_auto.fields["epv_margin"].profile_tag is None

    def test_declining_uses_latest_year(self):
        # KHC classifies declining(+cyclical): latest margin, declining wins
        h, _, a = fixture_assumptions("KHC")
        assert a.profile.primary == "declining"
        f = a.fields["epv_margin"]
        assert f.value == pytest.approx(_margin(h.periods[-1]))
        assert f.profile_tag == a.profile.tag
        assert "declining wins" in f.derivation

    def test_cyclical_uses_full_window(self):
        # forced reassignment exercises the cyclical (non-declining) rule
        h, _, a = fixture_assumptions("MSFT", profile="mature+cyclical")
        assert a.fields["epv_margin"].value == pytest.approx(
            _mean([_margin(p) for p in h.periods]))

    def test_collision_declining_beats_cyclical(self):
        h, _, a = fixture_assumptions("MSFT", profile="declining+cyclical")
        assert a.fields["epv_margin"].value == pytest.approx(
            _margin(h.periods[-1]))


# ── availability + the growth line ───────────────────────────────────────────

class TestStates:
    def test_negative_normalized_earnings_is_data_not_error(self):
        m = build_model(toy_history(), toy_market(), valuation_date=VD,
                        overrides={"epv_margin": -0.10}, profile=None)
        epv = next(mr for mr in m.methods if mr.id == "epv")
        assert not epv.availability.available
        assert epv.availability.reason_code == "epv_negative_earnings"
        assert any(w.code == "epv_negative_earnings" for w in m.warnings)
        assert not m.growth.available
        assert m.growth.reason_code == "epv_unavailable"
        # serialized: unavailable is a reasoned state, and the DCF legs live
        out = method_out(epv, price=50.0)
        assert out["available"] is False
        assert out["reason"]["code"] == "epv_negative_earnings"

    def test_value_destructive_is_labeled_never_negative(self):
        # EPV margin far above the projected margin → EPV > DCF
        m = build_model(toy_history(), toy_market(), valuation_date=VD,
                        overrides={"epv_margin": 0.55}, profile=None)
        assert m.growth.available
        assert m.growth.state == "value_destructive"
        assert m.growth.per_share < 0
        text = growth_out(m)["text"]
        assert "destroys value" in text
        assert "-$" not in text and "$-" not in text

    def test_epv_shares_the_bridge_and_the_wacc(self):
        # same bridge adjustment as the DCF legs; EV implied by the same WACC
        m = build_model(toy_history(), toy_market(), valuation_date=VD,
                        profile=None)
        adj = {mid: b.equity_value - b.enterprise_value
               for mid, b in m.bridges.items()}
        assert adj["epv"] == pytest.approx(adj["gordon"], rel=1e-12)
        assert adj["epv"] == pytest.approx(adj["exit_multiple"], rel=1e-12)


# ── the fourth-method contract ───────────────────────────────────────────────

def _stub_method():
    return MethodResult(
        id="stub_method", label="Stub method", order=99,
        availability=MethodAvailability(True), note="contract test",
        enterprise_value=123.0,
        bridge=None, detail=[])


class TestFourthMethodContract:
    def test_serializer_renders_an_unknown_method_generically(self):
        # method_out must not branch on ids: a brand-new method flows through
        m = build_model(toy_history(), toy_market(), valuation_date=VD,
                        profile=None)
        stub = next(mr for mr in m.methods if mr.id == "epv")
        clone = MethodResult(
            id="stub_method", label="Stub method", order=99,
            availability=stub.availability, note="contract test",
            enterprise_value=stub.enterprise_value, bridge=stub.bridge,
            detail=stub.detail)
        m.methods.append(clone)
        doc = serialize_model(m, None, None, None)
        ids = [mo["id"] for mo in doc["valuation"]]
        assert ids == ["gordon", "exit_multiple", "epv", "stub_method"]
        out = doc["valuation"][-1]
        assert out["label"] == "Stub method"
        assert out["value_per_share"] == pytest.approx(
            stub.bridge.value_per_share)

    def test_workbook_fails_loudly_without_a_block_builder(self, tmp_path):
        from excel.writer import write_workbook
        m = build_model(toy_history(), toy_market(), valuation_date=VD,
                        profile=None)
        m.methods.append(_stub_method())
        with pytest.raises(KeyError, match="no workbook block builder"):
            write_workbook(m, tmp_path / "stub.xlsx")

    def test_display_order_lives_in_the_registry(self):
        m = build_model(toy_history(), toy_market(), valuation_date=VD,
                        profile=None)
        assert [mr.id for mr in sorted(m.methods, key=lambda x: x.order)] \
            == ["gordon", "exit_multiple", "epv"]
