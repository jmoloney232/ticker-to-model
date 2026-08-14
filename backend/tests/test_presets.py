"""Preset tests (owner spec, 2026-08-14): identity reproduces the golden,
market_implied lands on the market price, downside never exceeds derived,
provenance end to end, YAML parity with the methodology surface, and the
guardrails — validation never bypassed, warnings never suppressed, unavailable
presets say so."""

from __future__ import annotations

import json as jsonlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from test_engine import GOLDEN_VD, VD, F, golden_dict, toy_history, toy_market

from engine.assumptions import derive_assumptions
from engine.dcf import build_model
from engine.errors import InvalidAssumptionError, PresetUnavailableError
from engine.presets import (
    BUILTIN_NAMES,
    PRESETS_PATH,
    Preset,
    PresetField,
    apply_preset,
    decode_assumption_set,
    encode_assumption_set,
    evaluate_rule,
    load_presets,
    rule_namespace,
)

PRESETS = load_presets()


def toy_with_preset(name, history=None, market=None):
    h = history or toy_history()
    mkt = market or toy_market()
    a = apply_preset(derive_assumptions(h, mkt), PRESETS[name], h, mkt, VD)
    return build_model(h, mkt, valuation_date=VD, assumptions=a)


def msft_with_preset(name):
    from test_fixtures_real import source_for
    from test_market import BrokenVendor

    from ingest.assemble import build_financial_history
    from ingest.cache import NullCache
    from market.assemble import build_market_inputs
    from market.provider import LadderedProvider

    h = build_financial_history("MSFT", source_for("MSFT"))
    provider = LadderedProvider(BrokenVendor(), BrokenVendor(), cache=NullCache())
    mi = build_market_inputs("MSFT", provider, as_of=GOLDEN_VD)
    a = apply_preset(derive_assumptions(h, mi), PRESETS[name], h, mi, GOLDEN_VD)
    return build_model(h, mi, valuation_date=GOLDEN_VD, assumptions=a)


class TestIdentity:
    def test_derived_preset_reproduces_the_golden_exactly(self):
        path = Path(__file__).parent / "fixtures" / "msft_model_golden.json"
        want = jsonlib.loads(path.read_text())
        got = golden_dict(msft_with_preset("derived"))
        assert set(want) == set(got)
        for key, expected in want.items():
            if isinstance(expected, float):
                assert got[key] == pytest.approx(expected, rel=1e-9), key
            else:
                assert got[key] == expected, key

    def test_derived_preset_marks_no_fields(self):
        m = toy_with_preset("derived")
        assert m.assumptions.active_preset == "derived"
        assert all(a.provenance == "derived"
                   for a in m.assumptions.fields.values())


class TestMarketImplied:
    def test_lands_on_the_market_price_toy(self):
        # price near the toy's own value so a solution exists (at the default
        # price of 50 the toy is worth ~7x its price and no terminal growth in
        # range bridges that — the solver honestly reports no solution)
        mkt = toy_market(price=300.0)
        m = toy_with_preset("market_implied", market=mkt)
        assert (m.bridges["gordon"].value_per_share
                == pytest.approx(300.0, rel=1e-4))
        tg = m.assumptions.fields["terminal_growth"]
        assert tg.provenance == "preset:market_implied"
        assert tg.preset_note.startswith("solved:")

    def test_lands_on_the_market_price_msft(self):
        m = msft_with_preset("market_implied")
        assert (m.bridges["gordon"].value_per_share
                == pytest.approx(m.market.price.value, rel=1e-4))

    def test_no_solution_below_wacc_reports_unavailable_with_reason(self):
        # a price no terminal growth below WACC can reach: unavailable, with
        # the reason named — never a fallback field or an extreme value
        h, mkt = toy_history(), toy_market(price=50_000.0)
        with pytest.raises(PresetUnavailableError) as exc:
            apply_preset(derive_assumptions(h, mkt),
                         PRESETS["market_implied"], h, mkt, VD)
        assert "below wacc" in str(exc.value).lower()


class TestStreetConvention:
    def test_rules_apply_with_provenance(self):
        m = toy_with_preset("street_convention")
        a = m.assumptions
        # terminal g = max(1.5%, rf 4%) = 4%; tax = marginal 25%;
        # capex = midpoint(8%, D&A/rev 8%) = 8% (toy is already at parity)
        assert a.eff("terminal_growth") == pytest.approx(0.04)
        assert a.eff("effective_tax_fy1") == pytest.approx(0.25)
        assert a.eff("capex_pct") == pytest.approx(0.08)
        assert (a.fields["terminal_growth"].provenance
                == "preset:street_convention")
        assert a.fields["terminal_growth"].preset_note.startswith("rule:")

    def test_g_at_or_above_wacc_still_blocks(self):
        # owner guardrail: presets never bypass validation. Tiny equity weight
        # drags WACC to ~4.3% while street g = rf = 5% -> hard block.
        h = toy_history()
        mkt = toy_market(price=1.0, rf=0.05, beta=0.0)
        a = apply_preset(derive_assumptions(h, mkt),
                         PRESETS["street_convention"], h, mkt, VD)
        with pytest.raises(InvalidAssumptionError) as exc:
            build_model(h, mkt, valuation_date=VD, assumptions=a)
        assert "below WACC" in str(exc.value)

    def test_warnings_not_suppressed_p5_names_the_preset(self):
        # street g (4%) above the default ceiling min(2.5%, 10Y) must warn
        # exactly as a user override would, with provenance named
        m = toy_with_preset("street_convention")
        p5 = m.checks.result("P5")
        assert p5.status == "warn"
        assert "preset:street_convention" in p5.detail


class TestDownside:
    def _varied_history(self):
        # rising revenues + one bad-margin year (FY2023: SG&A 250, EBIT 200)
        h = toy_history(revenues=(800.0, 900.0, 1000.0))
        p0 = h.periods[0]
        p0.income["selling_general_admin"] = F(250 * 0.8)
        p0.income["operating_income"] = F(200 * 0.8)
        return h

    def test_no_higher_than_derived_toy(self):
        h = self._varied_history()
        derived = build_model(h, toy_market(),
                              valuation_date=VD).bridges["gordon"].value_per_share
        down = toy_with_preset("downside",
                               history=self._varied_history()
                               ).bridges["gordon"].value_per_share
        assert down < derived

    def test_no_higher_than_derived_msft(self):
        derived = golden_dict(msft_with_preset("derived"))["per_share_gordon"]
        down = msft_with_preset("downside").bridges["gordon"].value_per_share
        assert down <= derived

    def test_rules_trace_to_the_filers_own_history(self):
        h = self._varied_history()
        m = toy_with_preset("downside", history=h)
        a = m.assumptions
        # worst year FY2023 (margin 20%): its SG&A ratio is 25%
        assert a.eff("sga_pct") == pytest.approx(0.25)
        # growth premium over terminal halved: cagr 11.8% -> 7.15%
        cagr = (1000 / 800) ** 0.5 - 1
        assert a.eff("revenue_growth_fy1") == pytest.approx(
            0.025 + (cagr - 0.025) / 2)
        # beta max(raw 1.0, 1.0) = 1.0 (unchanged for the toy)
        assert a.eff("beta") == pytest.approx(1.0)

    def test_growth_never_raised_for_decliners(self):
        h = toy_history(revenues=(1100.0, 1050.0, 1000.0))   # declining filer
        derived_g = derive_assumptions(h, toy_market()).eff("revenue_growth_fy1")
        m = toy_with_preset("downside", history=h)
        assert derived_g < 0.025
        assert m.assumptions.eff("revenue_growth_fy1") == pytest.approx(derived_g)

    def test_optional_cogs_skipped_for_by_nature(self):
        h = toy_history()
        h.cost_structure = "by_nature"
        m = toy_with_preset("downside", history=h)
        assert not m.assumptions.has("cogs_pct")     # structurally absent, skipped
        assert (m.assumptions.fields["sga_pct"].provenance
                == "preset:downside")


class TestGuardrailsAndApplicability:
    def test_unmet_applicability_says_so(self):
        p = Preset(name="x", title="x", rationale="r", builtin=False,
                   applicability={"cost_structure": ["by_nature"]}, fields={})
        h, mkt = toy_history(), toy_market()
        with pytest.raises(PresetUnavailableError) as exc:
            apply_preset(derive_assumptions(h, mkt), p, h, mkt, VD)
        assert "cost_structure" in str(exc.value)

    def test_min_history_years_says_so(self):
        p = Preset(name="x", title="x", rationale="r", builtin=False,
                   applicability={"min_history_years": 5}, fields={})
        h, mkt = toy_history(), toy_market()
        with pytest.raises(PresetUnavailableError) as exc:
            apply_preset(derive_assumptions(h, mkt), p, h, mkt, VD)
        assert "5 years" in str(exc.value)

    def test_absent_warnings_condition(self):
        p = Preset(name="x", title="x", rationale="r", builtin=False,
                   applicability={"absent_warnings": ["coverage_low"]}, fields={})
        h, mkt = toy_history(), toy_market()
        h.warnings.append(SimpleNamespace(code="coverage_low"))
        with pytest.raises(PresetUnavailableError) as exc:
            apply_preset(derive_assumptions(h, mkt), p, h, mkt, VD)
        assert "coverage_low" in str(exc.value)

    def test_missing_non_optional_field_says_so(self):
        p = Preset(name="x", title="x", rationale="r", builtin=False,
                   applicability={},
                   fields={"cogs_pct": PresetField(name="cogs_pct", form="rule",
                                                   rule="0.5")})
        h = toy_history()
        h.cost_structure = "by_nature"
        mkt = toy_market()
        with pytest.raises(PresetUnavailableError) as exc:
            apply_preset(derive_assumptions(h, mkt), p, h, mkt, VD)
        assert "cogs_pct" in str(exc.value)

    def test_preset_values_pass_domain_validation(self):
        p = Preset(name="x", title="x", rationale="r", builtin=False,
                   applicability={},
                   fields={"terminal_growth": PresetField(
                       name="terminal_growth", form="literal", value=0.30)})
        h, mkt = toy_history(), toy_market()
        with pytest.raises(InvalidAssumptionError):
            apply_preset(derive_assumptions(h, mkt), p, h, mkt, VD)

    def test_literal_form_tagged_in_provenance(self):
        p = Preset(name="myview", title="My view", rationale="", builtin=False,
                   applicability={},
                   fields={"erp": PresetField(name="erp", form="literal",
                                              value=0.055)})
        h, mkt = toy_history(), toy_market()
        a = apply_preset(derive_assumptions(h, mkt), p, h, mkt, VD)
        assert a.eff("erp") == 0.055
        assert a.fields["erp"].provenance == "preset:myview"
        assert "literal" in a.fields["erp"].preset_note


class TestProvenanceEndToEnd:
    def test_override_beats_preset_beats_derived(self):
        h, mkt = toy_history(), toy_market()
        a = apply_preset(derive_assumptions(h, mkt),
                         PRESETS["street_convention"], h, mkt, VD)
        m = build_model(h, mkt, valuation_date=VD, assumptions=a,
                        overrides={"terminal_growth": 0.02})
        tg = m.assumptions.fields["terminal_growth"]
        assert tg.effective == 0.02 and tg.provenance == "user"
        # preset layer still visible underneath (nothing erased)
        assert tg.preset_value == pytest.approx(0.04)
        # untouched preset field keeps preset provenance; others stay derived
        assert (m.assumptions.fields["effective_tax_fy1"].provenance
                == "preset:street_convention")
        assert m.assumptions.fields["rnd_pct"].provenance == "derived"

    def test_cli_renders_preset_and_provenance(self):
        from cli import render
        h, mkt = toy_history(), toy_market()
        a = apply_preset(derive_assumptions(h, mkt),
                         PRESETS["street_convention"], h, mkt, VD)
        m = build_model(h, mkt, valuation_date=VD, assumptions=a,
                        overrides={"capex_pct": 0.09})
        out = render(m, preset=PRESETS["street_convention"])
        assert "Preset: Street convention" in out
        assert "preset:street_convention" in out
        assert "USER (derived default 8.00%)" in out


class TestEncoding:
    def test_roundtrip(self):
        code = encode_assumption_set("downside", {"terminal_growth": 0.02,
                                                  "midyear": False})
        preset, overrides = decode_assumption_set(code)
        assert preset == "downside"
        assert overrides == {"terminal_growth": 0.02, "midyear": False}

    def test_roundtrip_empty(self):
        preset, overrides = decode_assumption_set(encode_assumption_set(None, None))
        assert preset is None and overrides == {}

    def test_compact_and_urlsafe(self):
        code = encode_assumption_set("street_convention", {"beta": 1.2})
        assert len(code) < 80
        assert all(c.isalnum() or c in "-_" for c in code)

    def test_garbage_rejected_with_message(self):
        with pytest.raises(ValueError, match="invalid assumption-set code"):
            decode_assumption_set("not-a-real-code!!")


class TestYamlParityWithMethodologySurface:
    def test_lives_beside_methodology_yaml(self):
        assert PRESETS_PATH.parent == (PRESETS_PATH.parent / "methodology.yaml").parent
        assert (PRESETS_PATH.parent / "methodology.yaml").exists()

    def test_builtins_complete_with_rationales(self):
        assert set(BUILTIN_NAMES) <= set(PRESETS)
        for name in BUILTIN_NAMES:
            p = PRESETS[name]
            assert p.builtin and p.rationale, name

    def test_every_rule_evaluates_on_a_real_namespace(self):
        h, mkt = toy_history(), toy_market()
        ns = rule_namespace(h, mkt, derive_assumptions(h, mkt))
        for p in PRESETS.values():
            for f in p.fields.values():
                if f.form == "rule":
                    assert isinstance(evaluate_rule(f.rule, ns), float), \
                        (p.name, f.name)

    def test_methodology_yaml_names_every_builtin(self):
        doc = yaml.safe_load((PRESETS_PATH.parent / "methodology.yaml").read_text())
        entry = next(e for e in doc["conventions"]
                     if e["id"] == "assumption_presets")
        for name in BUILTIN_NAMES:
            assert name in entry["default"], name

    def test_rule_evaluator_is_whitelist_only(self):
        ns = {"x": 2.0}
        assert evaluate_rule("min(x, 3) + max(1, -x) * abs(-2)", ns) == 4.0
        for evil in ("__import__('os')", "x.__class__", "(lambda: 1)()",
                     "[1][0]", "x if x else 0"):
            with pytest.raises(ValueError):
                evaluate_rule(evil, ns)
