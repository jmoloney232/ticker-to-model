"""Company profile classifier (owner-approved 2026-08-15): rules, guards,
derive-layer application, disclosure, and the structural invariant that a
profile can never move WACC."""

from pathlib import Path

import pytest
import yaml
from conftest import val
from test_api import edgar_for, provider_for
from test_engine import GOLDEN_VD, toy_history, toy_market

from engine.assumptions import derive_assumptions
from engine.profile import (
    CAPEX_DA_CAP,
    CAPEX_DA_MIN,
    COMPOUNDER_CAGR_MIN,
    COMPOUNDER_LATEST_MIN,
    EXCESS_RETURN_MIN,
    INFLATION_PROXY,
    MARGIN_RANGE_MIN,
    ProfileMeasures,
    classify,
    parse_profile,
)
from engine.projections import capex_path, growth_path
from engine.wacc import build_wacc
from ingest.assemble import build_financial_history
from market.assemble import build_market_inputs

METHODOLOGY = Path(__file__).parent.parent / "engine" / "methodology.yaml"


def measures(**over) -> ProfileMeasures:
    base = dict(cagr=0.05, g_latest=0.05, roic_median=0.15,
                roic_years_above_wacc=4, roic_years=4, wacc=0.09,
                margin_range=0.02, rev_down_years=0, capex_da=1.0, window=5)
    base.update(over)
    return ProfileMeasures(**base)


def fixture_assumptions(ticker, profile="auto"):
    h = build_financial_history(ticker, edgar_for(ticker))
    mkt = build_market_inputs(ticker, provider_for(ticker), as_of=GOLDEN_VD)
    return h, mkt, derive_assumptions(h, mkt, profile=profile)


def test_thresholds_match_methodology():
    doc = yaml.safe_load(METHODOLOGY.read_text())
    entry = next(c for c in doc["conventions"]
                 if c["id"] == "company_profiles")
    assert entry["thresholds"] == {
        "inflation_proxy": INFLATION_PROXY,
        "compounder_cagr_min": COMPOUNDER_CAGR_MIN,
        "compounder_latest_min": COMPOUNDER_LATEST_MIN,
        "excess_return_min": EXCESS_RETURN_MIN,
        "margin_range_min": MARGIN_RANGE_MIN,
        "capex_da_min": CAPEX_DA_MIN,
        "capex_da_cap": CAPEX_DA_CAP,
    }


class TestPrimaryRules:
    def test_compounder_needs_all_four_legs(self):
        good = measures(cagr=0.14, g_latest=0.18, roic_median=0.40)
        assert classify(good).primary == "compounder"
        # each leg individually disqualifies
        assert classify(measures(cagr=0.07, g_latest=0.18,
                                 roic_median=0.40)).primary == "mature"
        assert classify(measures(cagr=0.14, g_latest=0.028,
                                 roic_median=0.40)).primary == "mature"
        assert classify(measures(cagr=0.14, g_latest=0.18,
                                 roic_median=0.12)).primary == "mature"

    def test_rebound_guard_is_the_dal_case(self):
        # 20.7% CAGR off a shock trough, latest year decelerated to 2.8%
        dal = measures(cagr=0.207, g_latest=0.028, roic_median=0.159,
                       wacc=0.088)
        assert dal.roic_years_above_wacc == dal.roic_years
        assert classify(dal).primary == "mature"

    def test_durability_is_strict_every_year(self):
        # AMZN: three legs pass, one sub-WACC year disqualifies (owner call)
        amzn = measures(cagr=0.111, g_latest=0.124, roic_median=0.204,
                        wacc=0.11, roic_years_above_wacc=3, roic_years=4)
        assert classify(amzn).primary == "mature"

    def test_declining_needs_both_legs(self):
        assert classify(measures(cagr=0.015, g_latest=0.015)).primary == \
            "declining"
        # one leg above inflation: not declining
        assert classify(measures(cagr=0.015, g_latest=0.03)).primary == \
            "mature"
        assert classify(measures(cagr=0.03, g_latest=-0.02)).primary == \
            "mature"


class TestModifierRules:
    def test_cyclical_needs_a_revenue_down_year(self):
        assert "cyclical" in classify(
            measures(margin_range=0.12, rev_down_years=1)).modifiers
        cost_story = classify(measures(margin_range=0.12, rev_down_years=0))
        assert "cyclical" not in cost_story.modifiers
        assert any("cost story" in n for n in cost_story.notes)

    def test_reinvestment_band_and_sanity_cap(self):
        assert "reinvestment_heavy" in classify(
            measures(capex_da=2.5)).modifiers
        assert "reinvestment_heavy" not in classify(
            measures(capex_da=1.2)).modifiers
        mcd = classify(measures(capex_da=6.6))       # lessee-D&A limitation
        assert "reinvestment_heavy" not in mcd.modifiers
        assert any("sanity cap" in n for n in mcd.notes)

    def test_modifiers_layer_on_any_primary(self):
        p = classify(measures(cagr=-0.011, g_latest=-0.035,
                              margin_range=0.36, rev_down_years=2))
        assert p.primary == "declining" and p.modifiers == ("cyclical",)
        assert p.tag == "declining+cyclical"


class TestParse:
    def test_round_trips_and_rejects(self):
        assert parse_profile("compounder+cyclical") == \
            ("compounder", ("cyclical",))
        assert parse_profile("mature") == ("mature", ())
        with pytest.raises(ValueError):
            parse_profile("growth")
        with pytest.raises(ValueError):
            parse_profile("mature+turbo")
        with pytest.raises(ValueError):
            parse_profile("mature+cyclical+cyclical")


class TestFixtures:
    def test_msft_classifies_compounder_reinvestment_heavy(self):
        _, _, a = fixture_assumptions("MSFT")
        assert a.profile.tag == "compounder+reinvestment_heavy"
        assert a.eff("forecast_years") == 10
        assert a.eff("capex_fade") is True
        # g default at the 10Y ceiling, provenance discloses the profile
        f = a.fields["terminal_growth"]
        assert f.value == pytest.approx(0.0468, abs=1e-3)
        assert f.provenance == \
            "derived (profile: compounder+reinvestment_heavy)"

    def test_khc_declining_anchors_to_its_own_trajectory(self):
        h, _, a = fixture_assumptions("KHC")
        assert a.profile.primary == "declining"
        rev = [p.value("revenue") for p in h.periods]
        cagr = (rev[-1] / rev[0]) ** (1 / (len(rev) - 1)) - 1
        assert cagr < 0
        assert a.eff("terminal_growth") == pytest.approx(max(-0.02, cagr))

    def test_ko_mature_with_capex_fade(self):
        _, _, a = fixture_assumptions("KO")
        assert a.profile.primary == "mature"
        assert "reinvestment_heavy" in a.profile.modifiers
        # mature: horizon stays at today's default
        assert a.eff("forecast_years") == 5

    def test_wacc_is_structurally_untouchable(self):
        # the invariant the spec demands: profile application cannot move
        # WACC, because WACC is built from the base fields before the
        # profile rewrites anything
        for ticker in ("MSFT", "KO", "KHC", "COST"):
            h, mkt, profiled = fixture_assumptions(ticker)
            base = derive_assumptions(h, mkt, profile=None)
            assert build_wacc(h, mkt, profiled).wacc == \
                build_wacc(h, mkt, base).wacc, ticker

    def test_reassignment_disclosed(self):
        _, _, a = fixture_assumptions("MSFT", profile="mature")
        assert a.profile.reassigned is True
        assert a.profile.tag == "mature"
        # mature applies today's defaults — g back at the house cap
        assert a.eff("terminal_growth") == pytest.approx(0.025)
        assert a.eff("forecast_years") == 5


class TestFadeMechanics:
    def test_growth_fade_is_linear_for_every_profile(self):
        # The half-cosine compounder fade was REMOVED 2026-08-16 (owner
        # decision): the decomposition audit priced it at ≤ $2/share across
        # the whole cohort. One shape, endpoints exact, constant steps.
        _, _, a = fixture_assumptions("MSFT")     # compounder — no exception
        n = a.eff("forecast_years")
        path = growth_path(a)
        g1, gt = a.eff("revenue_growth_fy1"), a.eff("terminal_growth")
        assert len(path) == n
        assert path[0] == pytest.approx(g1) and path[-1] == pytest.approx(gt)
        steps = [y - x for x, y in zip(path, path[1:], strict=False)]
        assert all(s == pytest.approx(steps[0]) for s in steps)
        assert not a.has("fade_curved")           # the flag is gone, not off

    def test_capex_fades_to_maintenance(self):
        _, _, a = fixture_assumptions("MSFT")
        path = capex_path(a)
        assert path[0] == pytest.approx(a.eff("capex_pct"))
        assert path[-1] == pytest.approx(a.eff("capex_terminal_pct"))
        assert path[-1] < path[0]          # MSFT: 2.5× D&A fades down

    def test_flags_off_reproduce_flat_and_linear(self):
        h, mkt, _ = fixture_assumptions("MSFT")
        a = derive_assumptions(h, mkt, profile=None)
        assert capex_path(a) == [a.eff("capex_pct")] * 5
        path = growth_path(a)
        steps = [y - x for x, y in zip(path, path[1:], strict=False)]
        assert all(s == pytest.approx(steps[0]) for s in steps)   # linear


def test_house_cap_note_fires_on_the_profile_default():
    """The compounder default sits above the house cap — the disclosure
    must not disappear because the source is a default (owner ruling)."""
    from engine.dcf import build_model
    h = build_financial_history("MSFT", edgar_for("MSFT"))
    mkt = build_market_inputs("MSFT", provider_for("MSFT"), as_of=GOLDEN_VD)
    m = build_model(h, mkt, valuation_date=GOLDEN_VD)
    w = [w for w in m.warnings if w.code == "terminal_g_above_house_cap"]
    assert len(w) == 1 and "profile" in w[0].detail["provenance"]


def test_toy_flat_revenues_classify_declining():
    """The toy's flat revenues ARE a declining profile — which is why
    mechanics tests pin profile=None; classification is tested here."""
    a = derive_assumptions(toy_history(), toy_market())
    assert a.profile.primary == "declining"
    assert a.eff("terminal_growth") == pytest.approx(0.0)   # clamp(0% CAGR)


class TestProfileApi:
    def test_reassignment_via_api_is_disclosed(self):
        from fastapi.testclient import TestClient

        from app import create_app
        client = TestClient(create_app(edgar_for=edgar_for,
                                       provider_for=provider_for))
        auto = client.post("/api/model/MSFT", json={}).json()
        assert auto["profile"]["tag"] == "compounder+reinvestment_heavy"
        assert auto["profile"]["reassigned"] is False
        assert auto["profile"]["measures"]["cagr"] > 0.08

        # Part 3 (owner spec 2026-08-17): one plain-English line per option,
        # server-owned; the framing line refuses the price-hunting reading
        assert set(auto["profile"]["blurbs"]) == {
            "compounder", "mature", "declining",
            "cyclical", "reinvestment_heavy"}
        assert "not a way to land nearer the market price" in \
            auto["profile"]["framing"]

        re = client.post("/api/model/MSFT",
                         json={"profile": "mature"}).json()
        assert re["profile"]["tag"] == "mature"
        assert re["profile"]["reassigned"] is True
        # the auto classification survives a reassignment — both are visible
        assert re["profile"]["auto_tag"] == "compounder+reinvestment_heavy"
        rows = {a["name"]: a for a in re["assumptions"]}
        assert rows["forecast_years"]["value"] == 5
        # reassignment travels in the share code and reproduces the screen
        code = re["code"]
        via = client.get(f"/api/model/MSFT?code={code}").json()
        assert via["profile"]["tag"] == "mature"
        assert (val(via, "gordon")["value_per_share"]
                == val(re, "gordon")["value_per_share"])

        assert client.post("/api/model/MSFT",
                           json={"profile": "turbo"}).status_code == 400
