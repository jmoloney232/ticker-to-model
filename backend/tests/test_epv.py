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
from test_engine import GOLDEN_VD, VD, toy_history, toy_market
from test_profile import fixture_assumptions

from app.serialize import growth_out, method_out, serialize_model
from engine.assumptions import EPV_MARGIN_RULES, derive_assumptions
from engine.dcf import EPV_FIELDS, build_model
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
        "capex_pct": (a.eff("dep_pct_beginning_ppe")
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

    def test_declining_uses_window_median(self):
        # KHC classifies declining(+cyclical): window MEDIAN, declining wins
        # (owner 2026-08-16 — robust to the impairment year without any
        # non-recurring classification; the latest year IS the distortion)
        h, _, a = fixture_assumptions("KHC")
        assert a.profile.primary == "declining"
        f = a.fields["epv_margin"]
        margins = sorted(_margin(p) for p in h.periods)
        assert f.value == pytest.approx(margins[len(margins) // 2])
        assert f.value > 0                      # the -18.7% year is ignored
        assert f.profile_tag == a.profile.tag
        assert "MEDIAN" in f.derivation

    def test_cyclical_uses_full_window(self):
        # forced reassignment exercises the cyclical (non-declining) rule
        h, _, a = fixture_assumptions("MSFT", profile="mature+cyclical")
        assert a.fields["epv_margin"].value == pytest.approx(
            _mean([_margin(p) for p in h.periods]))

    def test_collision_declining_beats_cyclical(self):
        # declining's MEDIAN beats cyclical's MEAN over the same window
        h, _, a = fixture_assumptions("MSFT", profile="declining+cyclical")
        margins = sorted(_margin(p) for p in h.periods)
        assert a.fields["epv_margin"].value == pytest.approx(
            margins[len(margins) // 2])
        assert a.fields["epv_margin"].value != pytest.approx(
            _mean([_margin(p) for p in h.periods]))


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
        id="stub_method", label="Stub method", order=99, family="dcf",
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
            id="stub_method", label="Stub method", order=99, family="dcf",
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


# ── the DCF/EPV view split (owner-approved 2026-08-16) ───────────────────────

class TestFamilies:
    def test_methods_carry_their_family(self):
        m = build_model(toy_history(), toy_market(), valuation_date=VD,
                        profile=None)
        fams = {mr.id: mr.family for mr in m.methods}
        assert fams == {"gordon": "dcf", "exit_multiple": "dcf",
                        "epv": "epv"}

    def test_families_payload_is_server_owned(self):
        m = build_model(toy_history(), toy_market(), valuation_date=VD,
                        profile=None)
        doc = serialize_model(m, None, None, None)
        fams = doc["families"]
        assert [f["id"] for f in fams] == ["dcf", "epv"]
        assert fams[0]["fields"] is None            # DCF = full surface
        assert fams[1]["fields"] == list(EPV_FIELDS)
        for mo in doc["valuation"]:
            assert mo["family"] in {f["id"] for f in fams}


class TestEpvFieldContract:
    """EPV_FIELDS is exact — the EPV view's filtered assumptions surface is
    a tested contract, not a hand-maintained list. Every listed field moves
    the EPV value (under the toggles that route to it); every unlisted
    editable field leaves it bit-identical."""

    def _epv(self, overrides, **market_kwargs):
        m = build_model(toy_history(), toy_market(**market_kwargs),
                        valuation_date=VD, overrides=overrides, profile=None)
        return m.bridges["epv"].value_per_share

    def test_no_unlisted_field_moves_epv(self):
        from engine.assumptions import DISPLAY_ONLY
        base = self._epv({})
        a = derive_assumptions(toy_history(), toy_market(), profile=None)
        tested = []
        for f in a.fields.values():
            if f.name in EPV_FIELDS or f.name in DISPLAY_ONLY:
                continue
            v = f.effective
            if isinstance(v, bool):
                bumped = not v
            elif f.name == "forecast_years":
                bumped = 7
            elif v is None:
                continue
            else:
                bumped = v * 1.1 + 0.001
            assert self._epv({f.name: bumped}) == pytest.approx(
                base, rel=1e-12), f"{f.name} leaked into EPV"
            tested.append(f.name)
        assert len(tested) > 10        # the sweep actually swept

    # (base overrides, alt overrides, market kwargs) — pairs chosen so the
    # field's routing toggle is active (embedded Kd needs kd_synthetic off;
    # beta_adjusted needs a beta whose Blume adjustment differs from raw)
    MOVES = {
        "epv_margin": ({}, {"epv_margin": 0.30}, {}),
        "marginal_tax": ({}, {"marginal_tax": 0.30}, {}),
        "midyear": ({}, {"midyear": False}, {}),
        "beta": ({}, {"beta": 1.4}, {}),
        "beta_adjusted": ({}, {"beta_adjusted": False}, {"beta": 1.5}),
        "erp": ({}, {"erp": 0.06}, {}),
        "risk_free": ({}, {"risk_free": 0.05}, {}),
        "coverage_ratio": ({"coverage_ratio": 12.0},
                           {"coverage_ratio": 1.2}, {}),
        "kd_synthetic": ({"embedded_debt_rate": 0.09},
                         {"embedded_debt_rate": 0.09, "kd_synthetic": False},
                         {}),
        "embedded_debt_rate": ({"kd_synthetic": False,
                                "embedded_debt_rate": 0.06},
                               {"kd_synthetic": False,
                                "embedded_debt_rate": 0.08}, {}),
        "share_count": ({}, {"share_count": 12.0}, {}),
        "cash_floor_pct": ({}, {"cash_floor_pct": 0.05}, {}),
        # two-phase EPV (2026-08-17): the convergence target moves the
        # terminal capitalization rate; the horizon moves EPV only when the
        # path is non-flat (toy beta = 1 → flat), so pair it with a
        # terminal_beta that bends the path
        "terminal_beta": ({}, {"terminal_beta": 0.7}, {}),
        "forecast_years": ({"terminal_beta": 0.8},
                           {"terminal_beta": 0.8, "forecast_years": 10}, {}),
    }

    def test_every_listed_field_moves_epv(self):
        assert set(self.MOVES) == set(EPV_FIELDS)   # pairs cover the list
        for name, (base_o, alt_o, mk) in self.MOVES.items():
            lo = self._epv(base_o, **mk)
            hi = self._epv(alt_o, **mk)
            assert lo != pytest.approx(hi, rel=1e-9), \
                f"{name} is listed but does not move EPV"


class TestEpvVerdict:
    def test_ok_state_reads_below_price(self):
        h, mkt, _ = fixture_assumptions("MSFT")
        m = build_model(h, mkt, valuation_date=GOLDEN_VD)
        doc = serialize_model(m, None, None, None)
        v = doc["epv_verdict"]
        assert v["state"] == "ok"
        assert "no growth" in v["text"] and "below its" in v["text"]

    def test_negative_earnings_names_the_dcf_way_out(self):
        # toy with a stated negative margin (KHC no longer exercises this
        # state: the median rule gives it a positive normalized margin)
        m = build_model(toy_history(), toy_market(), valuation_date=VD,
                        overrides={"epv_margin": -0.10}, profile=None)
        doc = serialize_model(m, None, None, None)
        v = doc["epv_verdict"]
        assert v["state"] == "no_epv"
        assert "normalized operating margin is negative" in v["text"]
        assert "DCF view" in v["text"]

    def test_khc_declining_reads_value_destructive_now(self):
        # the median rule makes KHC's EPV available — and the growth line
        # correctly reads value-destructive (decline is worth less than
        # holding today's earnings power flat)
        h, mkt, _ = fixture_assumptions("KHC")
        m = build_model(h, mkt, valuation_date=GOLDEN_VD)
        assert m.growth.available
        assert m.growth.state == "value_destructive"

    def test_negative_equity_is_reframed_not_priced(self):
        m = build_model(toy_history(), toy_market(), valuation_date=VD,
                        overrides={"epv_margin": 0.001}, profile=None)
        assert m.bridges["epv"].value_per_share < 0    # the premise
        doc = serialize_model(m, None, None, None)
        v = doc["epv_verdict"]
        assert v["state"] == "negative_equity"
        assert "$-" not in v["text"] and "-$" not in v["text"]

    def test_growth_text_is_phrased_per_view(self):
        h, mkt, _ = fixture_assumptions("MSFT")
        m = build_model(h, mkt, valuation_date=GOLDEN_VD)
        g = serialize_model(m, None, None, None)["growth"]
        assert "rests on growth" in g["text"]           # DCF-side phrasing
        assert "on top of this" in g["epv_text"]        # EPV-side phrasing
        m2 = build_model(toy_history(), toy_market(), valuation_date=VD,
                         overrides={"epv_margin": 0.55}, profile=None)
        g2 = serialize_model(m2, None, None, None)["growth"]
        assert g2["state"] == "value_destructive"
        assert "destroys value" in g2["epv_text"]
