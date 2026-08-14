"""Engine tests (specs/04-engine.md, How tested).

The micro-case is a synthetic company small enough to verify every projected
line and the full DCF by hand — expected values are derived in comments from
first principles, never from the code under test.
"""

from __future__ import annotations

import random
from datetime import date

import pytest
import yaml

from engine.assumptions import SPREAD_TABLE, apply_overrides, derive_assumptions
from engine.dcf import build_model
from engine.errors import InvalidAssumptionError
from ingest.models import (
    CompanyMeta,
    Fact,
    FinancialHistory,
    FiscalPeriod,
    ValidationReport,
)
from market.models import Bar, BetaResult, MarketInputs, PricePoint, RatePoint

VD = date(2025, 12, 31)          # toy FYE_0 — textbook anchor


def F(value: float, source: str = "tag", unit: str = "USD") -> Fact:
    return Fact(value=value, unit=unit, tag="us-gaap:Toy", source=source)


def toy_period(fy: int, revenue: float = 1000.0) -> FiscalPeriod:
    """One toy fiscal year. At revenue=1000 every ratio is a round number:
    COGS 40%, R&D 10%, SG&A 20%, other opex 5% → EBIT 250 (25%).
    Interest 10 on debt 300 (3.333%); interest income 5 on cash 300 (1.667%).
    Tax 49/245 = 20%. D&A 80 on PP&E 800 (10%); capex 80 (8%); SBC 20 (2%).
    DSO 36.5, DIO 45.625, DPO 54.75. Dividends 98/196 = 50% payout.
    Assets 1400 = Liabilities 500 + Equity 900."""
    s = revenue / 1000.0
    p = FiscalPeriod(fiscal_year=fy, start=date(fy, 1, 1), end=date(fy, 12, 31),
                     duration_days=365, is_53_week=False)
    p.income = {
        "revenue": F(1000 * s), "cost_of_revenue": F(400 * s),
        "gross_profit": F(600 * s), "research_and_development": F(100 * s),
        "selling_general_admin": F(200 * s), "other_operating": F(50 * s),
        "operating_income": F(250 * s), "interest_expense": F(10 * s),
        "interest_income": F(5 * s), "pretax_income": F(245 * s),
        "income_tax": F(49 * s), "net_income": F(196 * s),
        "shares_basic_wa": F(10, unit="shares"),
        "shares_diluted_wa": F(10, unit="shares"),
    }
    p.balance = {
        "cash_and_equivalents": F(300 * s), "short_term_investments": F(0),
        "accounts_receivable": F(100 * s), "inventory": F(50 * s),
        "other_current_assets": F(25 * s), "ppe_net": F(800 * s),
        "goodwill": F(100 * s), "intangibles": F(0),
        "operating_lease_rou": F(0), "long_term_investments": F(0),
        "other_noncurrent_assets": F(25 * s), "total_assets": F(1400 * s),
        "accounts_payable": F(60 * s), "accrued_liabilities": F(40 * s),
        "short_term_debt": F(50 * s), "other_current_liabilities": F(30 * s),
        "deferred_revenue_current": F(20 * s), "long_term_debt": F(250 * s),
        "operating_lease_liability": F(0), "deferred_tax_liabilities": F(30 * s),
        "pension_liability": F(0, source="zero_logged"),
        "other_noncurrent_liabilities": F(20 * s), "total_liabilities": F(500 * s),
        "stockholders_equity": F(900 * s), "noncontrolling_interest": F(0),
        "preferred_equity": F(0), "temporary_equity": F(0),
    }
    p.cashflow = {
        "d_and_a": F(80 * s), "stock_compensation": F(20 * s),
        "capex": F(80 * s), "dividends_paid": F(98 * s),
    }
    return p


def toy_history(revenues=(1000.0, 1000.0, 1000.0)) -> FinancialHistory:
    periods = [toy_period(2023 + i, r) for i, r in enumerate(revenues)]
    return FinancialHistory(
        company=CompanyMeta(cik=1, ticker="TOY", name="Toy Co", sic=7372,
                            sic_description="Software", fye_anchor="1231"),
        periods=periods,
        shares_current=Fact(value=10, unit="shares", tag="dei:Toy", source="tag"),
        warnings=[], validation=ValidationReport(), staleness={},
        cost_structure="by_function")


def toy_market(price: float = 50.0, rf: float = 0.04,
               beta: float | None = 1.0) -> MarketInputs:
    b = None if beta is None else BetaResult(
        raw=beta, adjusted=2 / 3 * beta + 1 / 3, n_obs=104, r_squared=0.95,
        window_start=date(2023, 12, 31), window_end=VD, staleness="snapshot")
    return MarketInputs(ticker="TOY",
                        price=PricePoint(price, VD, "snapshot"),
                        risk_free=RatePoint(rf, VD, "snapshot"),
                        beta=b, warnings=[])


def toy_model(overrides=None, **kwargs):
    return build_model(toy_history(), toy_market(**kwargs), valuation_date=VD,
                       overrides=overrides)


# Hand-derived constants (comments show the arithmetic, not the code's):
#   Ke = 4% + 1.0×5% = 9%     (beta raw 1.0 → Blume-adjusted 1.0 exactly)
#   coverage = (3×250)/(3×10) = 25 → Aaa/AAA spread 0.40% → Kd = 4.4%
#     (Damodaran large-firm table, 2026-01)
#   Kd after tax = 4.4% × (1−25%) = 3.30%
#   E = 50×10 = 500, D = 300 → wE = 0.625, wD = 0.375
#   WACC = 0.625×9% + 0.375×3.30% = 5.625% + 1.2375% = 6.8625%
WACC = 0.068625


class TestMicroAssumptions:
    def test_every_default_hand_checked(self):
        a = derive_assumptions(toy_history(), toy_market())
        expected = {
            "revenue_growth_fy1": 0.0, "cogs_pct": 0.40, "rnd_pct": 0.10,
            "sga_pct": 0.20, "other_opex_pct": 0.05,
            # named lines sum exactly to revenue − EBIT → no identity gap
            "unclassified_costs_pct": 0.0,
            "da_pct_beginning_ppe": 0.10, "capex_pct": 0.08, "sbc_pct": 0.02,
            "dso": 36.5, "dio": 45.625, "dpo": 54.75,
            "oca_pct": 0.025, "accrued_pct": 0.04, "ocl_pct": 0.03,
            "defrev_pct": 0.02, "effective_tax_fy1": 0.20, "marginal_tax": 0.25,
            "payout_ratio": 0.50,
            "embedded_debt_rate": 10 / 300, "interest_income_yield": 5 / 300,
            "beta": 1.0, "erp": 0.05, "risk_free": 0.04, "coverage_ratio": 25.0,
            "terminal_growth": 0.025,
            # ROIC: IC = debt 300 + equity 900 − cash 300 = 900;
            # NOPAT = 250×(1−25%) = 187.5 → 20.8333%
            "terminal_roic": 187.5 / 900, "share_count": 10.0,
            # EV0 = 500 + 300 − 300 = 500; EBITDA0 = 250+80 = 330
            "exit_multiple": 500 / 330, "cash_floor_pct": 0.02,
        }
        for name, want in expected.items():
            assert a.eff(name) == pytest.approx(want), name

    def test_wacc_build_hand_checked(self):
        m = toy_model()
        w = m.wacc
        assert w.cost_of_equity == pytest.approx(0.09)
        assert w.rating == "Aaa/AAA" and w.spread == pytest.approx(0.004)
        assert w.kd_after_tax == pytest.approx(0.044 * 0.75)
        assert (w.weight_equity, w.weight_debt) == (pytest.approx(0.625),
                                                    pytest.approx(0.375))
        assert w.wacc == pytest.approx(WACC)
        assert w.gross_debt == pytest.approx(300)      # GROSS — invariant P3
        assert m.checks.result("P3").status == "pass"


class TestMicroProjections:
    def test_fy1_statements_hand_checked(self):
        # growth path fades 0% → 2.5%: FY1 growth = 0 → FY1 repeats FY0's
        # ratios exactly; equity rolls 900 + NI 196 − div 98 + SBC 20 = 1018;
        # assets-ex-cash 1100, L 500 → cash plug = 500+1018−1100 = 418;
        # CFO = 196+80+20−ΔNWC(0) = 296, CFI −80, CFF −98 → Δcash 118 = 418−300
        m = toy_model()
        fy1 = m.projections[0]
        for item, want in (("revenue", 1000.0), ("cost_of_revenue", 400.0),
                           ("operating_income", 250.0), ("interest_expense", 10.0),
                           ("interest_income", 5.0), ("pretax_income", 245.0),
                           ("income_tax", 49.0), ("net_income", 196.0)):
            assert fy1.income[item] == pytest.approx(want), item
        assert fy1.balance["ppe_net"] == pytest.approx(800.0)   # +80 capex −80 D&A
        assert fy1.balance["stockholders_equity"] == pytest.approx(1018.0)
        assert fy1.balance["cash_and_equivalents"] == pytest.approx(418.0)
        assert fy1.cashflow["cash_from_operations"] == pytest.approx(296.0)
        assert fy1.cashflow["net_change_in_cash"] == pytest.approx(118.0)

    def test_p1_p2_exact_every_year(self):
        m = toy_model()
        assert m.checks.result("P1").status == "pass"
        assert m.checks.result("P2").status == "pass"

    def test_ufcf_fy1_and_textbook_discounting(self):
        # mid-year off + vd = FYE_0 → exponents exactly 1..5 (invariant);
        # UFCF_1 = 250×(1−20%) + 80 − 80 − 0 = 200
        m = toy_model(overrides={"midyear": False})
        assert [y.exponent for y in m.ufcf] == [1, 2, 3, 4, 5]
        assert m.ufcf[0].ufcf == pytest.approx(200.0)
        assert m.ufcf[0].pv == pytest.approx(200.0 / (1 + WACC))

    def test_midyear_shifts_explicit_pvs_by_half_year_exactly(self):
        off = toy_model(overrides={"midyear": False})
        on = toy_model(overrides={"midyear": True})
        factor = (1 + WACC) ** 0.5
        for y_off, y_on in zip(off.ufcf, on.ufcf, strict=True):
            assert y_on.pv == pytest.approx(y_off.pv * factor)

    def test_exit_tv_keeps_full_period_asymmetry(self):
        # deliberate: Gordon shifts with mid-year, a sale does not
        m = toy_model(overrides={"midyear": True})
        assert m.terminal["gordon"].exponent == pytest.approx(4.5)
        assert m.terminal["exit_multiple"].exponent == pytest.approx(5.0)


class TestMicroTerminalAndBridge:
    def test_gordon_reinvestment_consistency_hand_checked(self):
        m = toy_model(overrides={"midyear": False})
        g, roic = 0.025, 187.5 / 900
        leg = m.terminal["gordon"]
        # RR = g/ROIC = 0.025/0.208333 = 12%; TV = NOPAT₆×(1−RR)/(WACC−g)
        assert leg.detail["reinvestment_rate"] == pytest.approx(g / roic)
        ebit5 = m.projections[-1].income["operating_income"]
        nopat6 = ebit5 * 1.025 * 0.75
        assert leg.detail["nopat_n1"] == pytest.approx(nopat6)
        assert leg.value_at_fyeN == pytest.approx(
            nopat6 * (1 - g / roic) / (WACC - g))
        # implied cross-check closes the loop
        ebitda5 = ebit5 + m.projections[-1].cashflow["d_and_a"]
        assert m.crosschecks["implied_exit_multiple"] == pytest.approx(
            leg.value_at_fyeN / ebitda5)

    def test_implied_g_from_exit_leg(self):
        m = toy_model()
        exit_leg = m.terminal["exit_multiple"]
        fcf_term = m.terminal["gordon"].detail["fcf_terminal"]
        assert m.crosschecks["implied_terminal_g"] == pytest.approx(
            m.wacc.wacc - fcf_term / exit_leg.value_at_fyeN)

    def test_bridge_hand_checked_with_source_labels(self):
        # excess cash = 300 + 0 − 2%×1000 = 280; equity = EV + 280 − 300 = EV−20
        m = toy_model()
        b = m.bridges["gordon"]
        items = {i.name: i for i in b.items}
        assert items["excess_cash"].value == pytest.approx(280.0)
        assert items["gross_debt"].value == pytest.approx(-300.0)
        assert b.equity_value == pytest.approx(b.enterprise_value - 20.0)
        assert b.value_per_share == pytest.approx(b.equity_value / 10.0)
        # a zero that means "unmapped" is labeled, never a bare zero
        assert items["pension_after_tax"].source == "zero_logged"


class TestProperties:
    def test_g_zero_means_no_reinvestment_and_perpetuity_tv(self):
        m = toy_model(overrides={"terminal_growth": 0.0, "midyear": False})
        leg = m.terminal["gordon"]
        assert leg.detail["reinvestment_rate"] == 0.0
        assert leg.value_at_fyeN == pytest.approx(
            leg.detail["nopat_n1"] / m.wacc.wacc)

    def test_value_monotonic_in_wacc_and_g(self):
        grid = toy_model().sensitivity["wacc_x_g"]
        for row in grid.cells:                       # g rises left→right: value up
            vals = [c for c in row if c is not None]
            assert vals == sorted(vals)
        for j in range(len(grid.cols)):              # WACC rises top→down: value down
            col = [row[j] for row in grid.cells if row[j] is not None]
            assert col == sorted(col, reverse=True)

    def test_sensitivity_center_equals_base_case(self):
        m = toy_model()
        for grid_name, base in (("wacc_x_g", m.bridges["gordon"].value_per_share),
                                ("wacc_x_multiple",
                                 m.bridges["exit_multiple"].value_per_share)):
            grid = m.sensitivity[grid_name]
            center = grid.cells[len(grid.rows) // 2][len(grid.cols) // 2]
            assert center == pytest.approx(base, rel=1e-12), grid_name

    def test_growth_cap_binds_and_uncapped_is_displayed(self):
        # revenue 1000 → 1600 → 2560: uncapped CAGR = 60%, default capped at 30%
        h = toy_history(revenues=(1000.0, 1600.0, 2560.0))
        m = build_model(h, toy_market(), valuation_date=VD)
        assert m.assumptions.eff("revenue_growth_fy1") == pytest.approx(0.30)
        assert m.assumptions.eff("revenue_cagr_uncapped") == pytest.approx(0.60)
        assert any(w.code == "growth_fade_steep" for w in m.warnings)

    def test_override_domain_validation(self):
        a = derive_assumptions(toy_history(), toy_market())
        with pytest.raises(InvalidAssumptionError) as exc:
            apply_overrides(a, {"payout_ratio": 1.4})
        assert "payout" in exc.value.user_message
        with pytest.raises(InvalidAssumptionError):
            apply_overrides(a, {"not_a_field": 1.0})

    def test_g_at_or_above_wacc_blocks(self):
        # ERP 1.2% → WACC ≈ 4.4%; terminal g override 5% must hard-block
        with pytest.raises(InvalidAssumptionError) as exc:
            toy_model(overrides={"erp": 0.012, "terminal_growth": 0.05})
        assert "WACC" in exc.value.user_message

    def test_roic_override_violating_rr_blocks(self):
        with pytest.raises(InvalidAssumptionError):
            toy_model(overrides={"terminal_roic": 0.02})   # ≤ g = 2.5%

    def test_degenerate_roic_falls_back_to_wacc_with_warning(self):
        # negative equity throughout → invested capital < 0 → no usable ROIC
        h = toy_history()
        for p in h.periods:
            p.balance["stockholders_equity"] = F(-950.0)
        m = build_model(h, toy_market(), valuation_date=VD)
        assert m.assumptions.fields["terminal_roic"].value is None
        assert any(w.code == "roic_fallback" and "value-neutral" in w.message
                   for w in m.warnings)
        assert m.terminal["gordon"].detail["roic"] == pytest.approx(m.wacc.wacc)

    def test_beta_fallback_flagged_in_p7(self):
        m = toy_model(beta=None)
        assert m.wacc.beta_used == 1.0
        assert m.wacc.beta_source == "fallback_1.0"
        assert "beta_fallback" in m.checks.result("P7").detail


class TestTerminalGrowthChecks:
    """Audit task 2: P5 polices the PUBLISHED constraint (g ≤ rf); the house
    cap draws only an info flag; the rf ceiling is displayed, never hidden."""

    def test_p5_warns_only_above_risk_free(self):
        # toy rf = 4%, WACC ≈ 6.86%: 5% is above rf → P5 warns
        m = toy_model(overrides={"terminal_growth": 0.05})
        assert m.checks.result("P5").status == "warn"
        assert "user" in m.checks.result("P5").detail

    def test_house_cap_band_draws_info_flag_not_p5(self):
        # 3% is above the 2.5% house cap but ≤ rf (4%) → info flag, P5 pass
        m = toy_model(overrides={"terminal_growth": 0.03})
        assert m.checks.result("P5").status == "pass"
        flags = [w for w in m.warnings if w.code == "terminal_g_above_house_cap"]
        assert flags and flags[0].severity == "info"
        assert "house cap" in flags[0].message

    def test_derived_default_draws_neither(self):
        m = toy_model()
        assert m.checks.result("P5").status == "pass"
        assert "terminal_g_above_house_cap" not in {w.code for w in m.warnings}

    def test_rf_ceiling_displayed_beside_the_default(self):
        m = toy_model()
        assert m.assumptions.eff("terminal_growth_rf_ceiling") == \
            pytest.approx(0.04)
        from engine.assumptions import DISPLAY_ONLY
        assert "terminal_growth_rf_ceiling" in DISPLAY_ONLY


class TestRatingBrackets:
    """The audit's headline bug: the old table truncated the distressed range
    at 'coverage > 0 → 4%', charging 0.5x-coverage filers a 4% spread where
    the published table charges 16%."""

    def test_bracket_assignment_spans_the_distressed_range(self):
        from engine.assumptions import rating_for_coverage
        cases = [(25.0, "Aaa/AAA", 0.0040), (7.0, "Aa2/AA", 0.0055),
                 (4.25, "A3/A-", 0.0089),   # boundary: > is strict
                 (2.6, "Baa2/BBB", 0.0111), (1.6, "B2/B", 0.0321),
                 (1.0, "Caa/CCC", 0.0885), (0.7, "Ca2/CC", 0.1261),
                 (0.5, "C2/C", 0.1600),     # the audit's example: was 4%
                 (0.1, "D2/D", 0.1900), (-3.0, "D2/D", 0.1900)]
        for coverage, rating, spread in cases:
            got_r, got_s = rating_for_coverage(coverage)
            assert (got_r, got_s) == (rating, spread), coverage

    def test_no_traceable_interest_gets_top_bracket(self):
        from engine.assumptions import rating_for_coverage
        assert rating_for_coverage(None) == ("Aaa/AAA", 0.0040)

    def test_distressed_warning_fires_with_recovery_pointer(self):
        # coverage overridden to 1.0 → Caa/CCC (8.85%) → the warning fires
        m = toy_model(overrides={"coverage_ratio": 1.0})
        assert m.wacc.spread >= 0.0885
        codes = {w.code for w in m.warnings}
        assert "synthetic_rating_distressed" in codes
        msg = next(w.message for w in m.warnings
                   if w.code == "synthetic_rating_distressed")
        assert "reverse-DCF" in msg and "going-concern" in msg

    def test_investment_grade_does_not_draw_distressed_warning(self):
        m = toy_model()
        assert "synthetic_rating_distressed" not in {w.code for w in m.warnings}

    def test_rating_table_staleness_flag(self):
        from datetime import date as _date
        m = build_model(toy_history(), toy_market(),
                        valuation_date=_date(2028, 6, 30))
        stale = [w for w in m.warnings if w.code == "rating_table_stale"]
        assert stale and stale[0].severity == "info"
        m2 = toy_model()          # golden-era vd: within 18 months, no flag
        assert "rating_table_stale" not in {w.code for w in m2.warnings}


class TestSpreadTableParity:
    def test_engine_constants_match_methodology_yaml(self):
        doc = yaml.safe_load(
            open("engine/methodology.yaml"))          # noqa: SIM115 — test-only I/O
        entry = next(c for c in doc["conventions"] if c["id"] == "cost_of_debt")
        yaml_rows = [(r["coverage_above"], r["rating"], r["spread"])
                     for r in entry["spread_table"]]
        assert yaml_rows == list(SPREAD_TABLE)
        from engine.assumptions import RATING_TABLE_AS_OF
        assert entry["spread_table_as_of"] == (
            f"{RATING_TABLE_AS_OF[0]}-{RATING_TABLE_AS_OF[1]:02d}")


class TestFixtureInvariants:
    """P1–P4 on real filers + randomized-override fuzz (spec 04, How tested)."""

    def _model(self, ticker, overrides=None, seed_bars=7):
        from test_fixtures_real import source_for
        from test_market import correlated_pair

        from ingest.assemble import build_financial_history
        from market.assemble import build_market_inputs
        from market.provider import StaticMarketProvider

        h = build_financial_history(ticker, source_for(ticker))
        stock, spy = correlated_pair(slope=1.1, seed=seed_bars)
        stock = [Bar(day=b.day, close=b.close * 3) for b in stock]
        mi = build_market_inputs(ticker, StaticMarketProvider(
            {ticker: stock, "SPY": spy}), as_of=date(2026, 8, 12))
        return build_model(h, mi, valuation_date=date(2026, 8, 14),
                           overrides=overrides)

    @pytest.mark.parametrize("ticker", ["MSFT", "KO", "GOOGL", "MCD"])
    def test_invariants_hold_on_real_filers(self, ticker):
        m = self._model(ticker)
        for check in ("P1", "P2", "P3", "P4"):
            assert m.checks.result(check).status == "pass", (ticker, check)
        assert m.bridges["gordon"].value_per_share > 0

    def test_randomized_overrides_never_break_the_identities(self):
        rng = random.Random(2026)
        for _ in range(10):
            overrides = {
                "revenue_growth_fy1": rng.uniform(-0.20, 0.45),
                "terminal_growth": rng.uniform(0.005, 0.03),
                "payout_ratio": rng.uniform(0.0, 1.0),
                "capex_pct": rng.uniform(0.01, 0.20),
                "effective_tax_fy1": rng.uniform(0.10, 0.35),
            }
            m = self._model("KO", overrides=overrides)
            assert m.checks.result("P1").status == "pass", overrides
            assert m.checks.result("P2").status == "pass", overrides


# ── Diagnostic-pass fixes (owner-approved 2026-08-14) ────────────────────────

class TestUnclassifiedCosts:
    """Margin-identity closure: costs in no named line item are projected as
    an explicit ratio, so projected EBIT margin reproduces the filer's own
    historical margin structure BY IDENTITY (the MCD-class bug #2 fix)."""

    def test_cost_hole_closes_margin_identity_exactly(self):
        # Punch an MCD-shaped hole: other_operating shrinks to a token $1
        # (real-but-tiny tag) while filed EBIT stays 250 → 49/1000 of revenue
        # lives in no named line.
        h = toy_history()
        for p in h.periods:
            p.income["other_operating"] = F(1.0)
        a = derive_assumptions(h, toy_market())
        assert a.eff("unclassified_costs_pct") == pytest.approx(0.049)
        m = build_model(h, toy_market(), valuation_date=VD)
        fy1 = m.projections[0]
        # without the closure line this margin would be 29.9%, not 25%
        assert (fy1.income["operating_income"] / fy1.income["revenue"]
                == pytest.approx(0.25))
        assert fy1.income["unclassified_costs"] == pytest.approx(49.0)
        assert any(w.code == "unclassified_costs" for w in m.warnings)

    def test_warning_names_the_percentage(self):
        h = toy_history()
        for p in h.periods:
            p.income["other_operating"] = F(1.0)
        m = build_model(h, toy_market(), valuation_date=VD)
        w = next(w for w in m.warnings if w.code == "unclassified_costs")
        assert "4.9%" in w.message
        assert w.detail["unclassified_costs_pct"] == pytest.approx(0.049)

    def test_below_one_percent_of_revenue_no_warning(self):
        # 0.5%-of-revenue gap: line still projected (identity exact), but no
        # warning — same materiality leg as H2's revenue cutoff.
        h = toy_history()
        for p in h.periods:
            p.income["other_operating"] = F(45.0)
        m = build_model(h, toy_market(), valuation_date=VD)
        assert m.assumptions.eff("unclassified_costs_pct") == pytest.approx(0.005)
        assert not any(w.code == "unclassified_costs" for w in m.warnings)


class TestNegativeTerminalAnchor:
    """Owner-approved: a perpetuity or multiple on a negative FY5 base reports
    an honest unavailable state, never a negative 'value'. Reverse DCF stays
    available for these filers (BA's implied recovery margin is the point)."""

    def _distressed(self, ebit=-50.0):
        # filed EBIT negative; cost lines unchanged → the identity-gap line
        # carries the distress into the projection (margin = filed margin)
        h = toy_history()
        for p in h.periods:
            p.income["operating_income"] = F(ebit)
        return h

    def test_gordon_unavailable_exit_survives(self):
        # EBIT −5% but EBITDA_N ≈ +3% → gordon out, exit leg still a value
        m = build_model(self._distressed(-50.0), toy_market(), valuation_date=VD)
        assert "gordon" not in m.terminal and "gordon" not in m.bridges
        assert "exit_multiple" in m.bridges
        w = [w for w in m.warnings if w.code == "terminal_anchor_negative"]
        assert len(w) == 1 and w[0].detail["leg"] == "gordon"
        assert "wacc_x_g" not in m.sensitivity
        assert m.checks.result("P8").status == "warn"

    def test_both_legs_unavailable_builds_honestly(self):
        # EBIT −15%: EBITDA0 < 0 → exit multiple None; FY5 NOPAT < 0 → no leg
        m = build_model(self._distressed(-150.0), toy_market(), valuation_date=VD)
        assert m.bridges == {} and m.terminal == {}
        assert any(w.code == "terminal_anchor_negative" for w in m.warnings)
        assert m.sensitivity == {}

    def test_reverse_dcf_still_informative(self):
        # the implied recovery margin must solve even when the model refuses
        # to print a value (raw Gordon math stays available to the solver)
        from engine.reverse import implied_assumption
        h = self._distressed(-150.0)
        r = implied_assumption(h, toy_market(), "ebitda_margin", VD,
                               target_price=20.0)
        assert r.status == "solved"
        assert r.implied > r.derived


# ── MSFT golden + sanity (fully offline: EDGAR + market snapshots) ───────────

GOLDEN_PATH = None  # set lazily below to tests/fixtures/msft_model_golden.json
GOLDEN_VD = date(2026, 8, 14)


def msft_model():
    from test_fixtures_real import source_for
    from test_market import BrokenVendor

    from ingest.assemble import build_financial_history
    from ingest.cache import NullCache
    from market.assemble import build_market_inputs
    from market.provider import LadderedProvider

    h = build_financial_history("MSFT", source_for("MSFT"))
    provider = LadderedProvider(BrokenVendor(), BrokenVendor(), cache=NullCache())
    mi = build_market_inputs("MSFT", provider, as_of=GOLDEN_VD)
    return build_model(h, mi, valuation_date=GOLDEN_VD)


def golden_dict(m) -> dict:
    fy1, fy5 = m.projections[0], m.projections[-1]
    return {
        "ticker": m.ticker,
        "valuation_date": m.valuation_date.isoformat(),
        "wacc": m.wacc.wacc, "cost_of_equity": m.wacc.cost_of_equity,
        "kd_after_tax": m.wacc.kd_after_tax, "coverage": m.wacc.coverage,
        "rating": m.wacc.rating, "beta_used": m.wacc.beta_used,
        "market_cap": m.wacc.market_cap, "gross_debt": m.wacc.gross_debt,
        "share_count": m.assumptions.eff("share_count"),
        "tv_gordon_pv": m.terminal["gordon"].pv,
        "tv_exit_pv": m.terminal["exit_multiple"].pv,
        "ev_gordon": m.bridges["gordon"].enterprise_value,
        "ev_exit": m.bridges["exit_multiple"].enterprise_value,
        "per_share_gordon": m.bridges["gordon"].value_per_share,
        "per_share_exit": m.bridges["exit_multiple"].value_per_share,
        "price": m.market.price.value,
        "implied_exit_multiple": m.crosschecks["implied_exit_multiple"],
        "implied_terminal_g": m.crosschecks["implied_terminal_g"],
        "fy1_revenue": fy1.income["revenue"], "fy5_revenue": fy5.income["revenue"],
        "fy1_ebit": fy1.income["operating_income"],
        "fy5_ebit": fy5.income["operating_income"],
        "fy1_ufcf": m.ufcf[0].ufcf, "fy5_ufcf": m.ufcf[-1].ufcf,
        "engine_warning_codes": sorted({w.code for w in m.warnings}),
        "check_statuses": {r.check_id: r.status for r in m.checks.results},
    }


class TestMSFTGoldenAndSanity:
    def test_golden_model_frozen(self, request):
        """Any change that shifts the MSFT valuation shows up as a diff here,
        never as a surprise. Regenerate ONLY deliberately: delete the golden
        file, re-run, review the new values, commit with a note."""
        import json as jsonlib
        from pathlib import Path
        path = Path(__file__).parent / "fixtures" / "msft_model_golden.json"
        got = golden_dict(msft_model())
        if not path.exists():
            path.write_text(jsonlib.dumps(got, indent=2, sort_keys=True) + "\n")
            pytest.fail(f"golden created at {path.name} — review the values, "
                        "commit, and re-run")
        want = jsonlib.loads(path.read_text())
        assert set(want) == set(got)
        for key, expected in want.items():
            if isinstance(expected, float):
                assert got[key] == pytest.approx(expected, rel=1e-9), key
            else:
                assert got[key] == expected, key

    def test_sanity_value_within_plausible_band_of_market_cap(self):
        """Not because the market is right — because a model 10× off has a bug,
        not an insight (owner requirement)."""
        m = msft_model()
        mcap = m.wacc.market_cap
        for method, bridge in m.bridges.items():
            ratio = bridge.equity_value / mcap
            assert 0.2 < ratio < 5.0, (method, ratio)
