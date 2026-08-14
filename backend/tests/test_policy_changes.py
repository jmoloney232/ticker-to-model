"""Owner-approved policy changes from the bulk-scan review (items 2–8):
cost-structure classification, derived EBIT, window trim, unsplit investments,
dual-class share derivation, H2 definitional mismatches, split labeling, and
the known-unsupported list — plus the final hardening round (2026-08-13):
H2 materiality band and the coverage gate."""

from datetime import date

import pytest
from conftest import M, dur, inst, make_source, model_year

from ingest.assemble import _coverage_gate, build_financial_history
from ingest.errors import (
    InsufficientCoverageError,
    KnownUnsupportedError,
    MissingRequiredItemError,
)
from ingest.mapping import MappedHistory, map_history
from ingest.models import Coverage, Fact, FiscalPeriod, UnmappedTag
from ingest.schema import load_schema
from ingest.validation import validate_history


def _map(facts, years=5):
    return map_history(facts, load_schema(), "1231", "SYN", years=years)


# ── Item 4: cost_structure ───────────────────────────────────────────────────

class TestCostStructure:
    def test_by_function_is_the_default_classification(self, clean_facts):
        m = _map(clean_facts)
        assert m.cost_structure == "by_function"

    def test_by_nature_filer_builds_without_cogs(self, clean_facts):
        gaap = clean_facts["facts"]["us-gaap"]
        del gaap["CostOfRevenue"]
        del gaap["GrossProfit"]
        m = _map(clean_facts)
        assert m.cost_structure == "by_nature"
        p = m.periods[-1]
        # absent, never zero — gross profit must not collapse to revenue
        assert "cost_of_revenue" not in p.income
        assert "gross_profit" not in p.income
        # the whole cost block lands in the operating residual (correct by-nature
        # semantics): revenue − R&D − SG&A − EBIT == what COGS was
        assert p.income["other_operating"].value == pytest.approx(model_year(2024)["cogs"])

    def test_pl3_is_gated_on_cost_structure(self, clean_facts):
        gaap = clean_facts["facts"]["us-gaap"]
        del gaap["CostOfRevenue"]
        del gaap["GrossProfit"]
        report = validate_history(_map(clean_facts))
        pl3 = report.result("PL3")
        assert pl3.status == "skipped"
        assert "by_nature" in pl3.detail

    def test_mixed_window_classifies_by_nature(self, clean_facts):
        # COGS filed in all years except the oldest (the GE FY2021 shape)
        gaap = clean_facts["facts"]["us-gaap"]
        gaap["CostOfRevenue"]["units"]["USD"] = [
            f for f in gaap["CostOfRevenue"]["units"]["USD"]
            if not f["end"].startswith("2020")]
        del gaap["GrossProfit"]
        assert _map(clean_facts).cost_structure == "by_nature"


# ── Item 5: derived EBIT ─────────────────────────────────────────────────────

class TestDerivedEbit:
    def test_ebit_derived_with_visible_warning(self, clean_facts):
        del clean_facts["facts"]["us-gaap"]["OperatingIncomeLoss"]
        m = _map(clean_facts)
        p = m.periods[-1]
        oi = p.income["operating_income"]
        assert oi.source == "derived"
        # pretax + interest expense − interest income recovers EBIT exactly in
        # the synthetic filer (it has no other non-operating items)
        assert oi.value == pytest.approx(model_year(2024)["oi"])
        warns = [w for w in m.warnings if w.code == "ebit_derived"]
        assert warns and "magnitude unknown" in warns[-1].message

    def test_absorbed_magnitude_reported_when_filer_tags_nonoperating(self, clean_facts):
        gaap = clean_facts["facts"]["us-gaap"]
        del gaap["OperatingIncomeLoss"]
        gaap["NonoperatingIncomeExpense"] = {
            "units": {"USD": [dur(25 * M, y) for y in range(2020, 2025)]}}
        m = _map(clean_facts)
        warns = [w for w in m.warnings if w.code == "ebit_derived"]
        assert warns and "25,000,000" in warns[-1].message


# ── Item 6: window trim for required-missing-oldest-years ────────────────────

class TestRequiredWindowTrim:
    PRETAX = ("IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
              "ExtraordinaryItemsNoncontrollingInterest")

    def _drop_pretax(self, facts, year):
        node = facts["facts"]["us-gaap"][self.PRETAX]
        node["units"]["USD"] = [f for f in node["units"]["USD"]
                                if not f["end"].startswith(str(year))]

    def test_oldest_year_missing_trims_with_warning(self, clean_facts):
        self._drop_pretax(clean_facts, 2020)
        m = _map(clean_facts)
        assert [p.fiscal_year for p in m.periods] == [2021, 2022, 2023, 2024]
        w = [w for w in m.warnings if w.code == "history_trimmed_required"]
        assert w and "pretax_income" in w[0].message and "FY2020" in w[0].message

    def test_recent_year_missing_still_errors(self, clean_facts):
        self._drop_pretax(clean_facts, 2024)
        with pytest.raises(MissingRequiredItemError) as exc:
            _map(clean_facts)
        assert exc.value.detail["item"] == "pretax_income"

    def test_three_year_floor_enforced(self, clean_facts):
        for y in (2020, 2021, 2022):
            self._drop_pretax(clean_facts, y)
        with pytest.raises(MissingRequiredItemError):
            _map(clean_facts)   # only FY2023–24 would remain


# ── Item 8: unsplit investments ──────────────────────────────────────────────

class TestUnsplitInvestments:
    def test_combined_tag_maps_with_disclosure(self, clean_facts):
        gaap = clean_facts["facts"]["us-gaap"]
        gaap["AvailableForSaleSecuritiesDebtSecurities"] = {
            "units": {"USD": [inst(500 * M, y) for y in range(2020, 2025)]}}
        m = _map(clean_facts)
        p = m.periods[-1]
        item = p.balance["investments_combined_unsplit"]
        assert item.value == pytest.approx(500 * M)
        assert item.tag == "us-gaap:AvailableForSaleSecuritiesDebtSecurities"
        warns = [w for w in m.warnings if w.code == "unsplit_investments"]
        assert warns and "excluded from net debt" in warns[-1].message

    def test_not_mapped_when_split_items_exist(self, clean_facts):
        gaap = clean_facts["facts"]["us-gaap"]
        gaap["ShortTermInvestments"] = {
            "units": {"USD": [inst(100 * M, y) for y in range(2020, 2025)]}}
        gaap["AvailableForSaleSecuritiesDebtSecurities"] = {
            "units": {"USD": [inst(500 * M, y) for y in range(2020, 2025)]}}
        m = _map(clean_facts)
        assert "investments_combined_unsplit" not in m.periods[-1].balance
        assert not [w for w in m.warnings if w.code == "unsplit_investments"]


# ── Item 3: dual-class share derivation ──────────────────────────────────────

class TestDualClassShares:
    def _make_dual_class(self, facts):
        """No undimensioned share tags anywhere (GOOGL/META shape) — but
        consolidated EPS is filed."""
        gaap = facts["facts"]["us-gaap"]
        del gaap["WeightedAverageNumberOfSharesOutstandingBasic"]
        del gaap["WeightedAverageNumberOfDilutedSharesOutstanding"]
        del facts["facts"]["dei"]["EntityCommonStockSharesOutstanding"]
        for y in range(2020, 2025):
            ni = model_year(y)["ni"]
            gaap.setdefault("EarningsPerShareBasic", {"units": {"USD/shares": []}})[
                "units"]["USD/shares"].append(dur(ni / (100 * M), y))
            gaap.setdefault("EarningsPerShareDiluted", {"units": {"USD/shares": []}})[
                "units"]["USD/shares"].append(dur(ni / (102 * M), y))

    def test_wa_shares_derived_from_ni_over_eps(self, clean_facts):
        self._make_dual_class(clean_facts)
        h = build_financial_history("SYN", make_source(clean_facts))
        p = h.latest
        assert p.income["shares_basic_wa"].source == "derived"
        assert p.income["shares_basic_wa"].value == pytest.approx(100 * M, rel=1e-6)
        assert p.income["shares_diluted_wa"].value == pytest.approx(102 * M, rel=1e-6)
        # provenance is loud: per-share value is the app's headline number
        assert any(w.code == "share_count_derived" for w in h.warnings)

    def test_cover_count_falls_back_to_wa_with_warning(self, clean_facts):
        self._make_dual_class(clean_facts)   # no dei fact, no us-gaap count
        h = build_financial_history("SYN", make_source(clean_facts))
        assert h.shares_current.source == "derived"
        assert h.shares_current.value == pytest.approx(100 * M, rel=1e-6)

    def test_usgaap_cover_count_preferred_over_wa_fallback(self, clean_facts):
        self._make_dual_class(clean_facts)
        clean_facts["facts"]["us-gaap"]["CommonStockSharesOutstanding"] = {
            "units": {"shares": [inst(98 * M, 2024)]}}
        h = build_financial_history("SYN", make_source(clean_facts))
        assert h.shares_current.tag == "us-gaap:CommonStockSharesOutstanding"
        assert h.shares_current.value == 98 * M


# ── Item 2: H2 definitional mismatches ───────────────────────────────────────

class TestH2Definitional:
    def _widen_cf_definition(self, facts, with_alt_tag):
        """CF reconciles a broader cash total: flows and reported net change
        gain +10M/yr (restricted cash growing) while BS plain cash does not."""
        gaap = facts["facts"]["us-gaap"]
        for tag in ("NetCashProvidedByUsedInOperatingActivities",
                    "CashAndCashEquivalentsPeriodIncreaseDecrease"):
            for f in gaap[tag]["units"]["USD"]:
                f["val"] += 10 * M
        if with_alt_tag:
            gaap["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
                 "IncludingDisposalGroupAndDiscontinuedOperations"] = {
                "units": {"USD": [
                    inst(model_year(y)["cash"] + 10 * M * (y - 2019), y)
                    for y in range(2020, 2025)]}}

    def test_alt_definition_tie_warns_not_fails(self, clean_facts):
        self._widen_cf_definition(clean_facts, with_alt_tag=True)
        report = validate_history(_map(clean_facts))
        h2 = report.result("H2")
        assert h2.status == "warn"
        assert "DEFINITIONAL" in h2.detail and "broader cash definition" in h2.detail
        assert report.overall == "pass_with_warnings"

    def test_ncc_tie_rescue_warns_not_fails(self, clean_facts):
        self._widen_cf_definition(clean_facts, with_alt_tag=False)
        report = validate_history(_map(clean_facts))
        h2 = report.result("H2")
        assert h2.status == "warn"
        assert "narrower definition" in h2.detail

    def test_real_break_still_fails(self, clean_facts):
        gaap = clean_facts["facts"]["us-gaap"]
        for f in gaap["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"]:
            f["val"] += 50 * M      # flows tie to NOTHING: not ncc, not any Δcash
            # (50M ≈ 4% of revenue — above the materiality band, so still fatal;
            # sub-materiality breaks are TestH2Materiality's subject)
        report = validate_history(_map(clean_facts))
        assert report.result("H2").status == "fail"
        assert report.overall == "fail"


# ── Item 7: split labeling ───────────────────────────────────────────────────

def test_share_unit_recasts_labeled_split_not_restated(clean_facts):
    gaap = clean_facts["facts"]["us-gaap"]
    gaap["WeightedAverageNumberOfSharesOutstandingBasic"]["units"]["shares"].append(
        dur(200 * M, 2021, accn="acc-split", filed="2024-02-15"))
    m = _map(clean_facts)
    codes = {w.code for w in m.warnings if w.item == "shares_basic_wa"}
    assert "split_adjustment" in codes and "restated" not in codes
    report = validate_history(m)
    assert report.result("H6").status == "pass"   # splits are not restatements


# ── XOM / NEE: known-unsupported list ────────────────────────────────────────

def test_known_unsupported_returns_honest_message():
    with pytest.raises(KnownUnsupportedError) as exc:
        build_financial_history("XOM", make_source())
    assert "custom extension tags" in exc.value.user_message
    assert exc.value.detail["ticker"] == "XOM"


def test_nee_known_unsupported_for_extension_tag_capex():
    with pytest.raises(KnownUnsupportedError) as exc:
        build_financial_history("NEE", make_source())
    assert "extension tags" in exc.value.user_message
    assert exc.value.detail["ticker"] == "NEE"


# ── Final round: H2 materiality band (owner-approved 1% rev + 5% flows) ──────

class TestH2Materiality:
    """An unreconciled residual below BOTH legs builds with the distinct
    'immaterial' outcome, quantified per year; above either leg it fails.
    Cutoffs calibrated on the scan: AMZN/TSLA/F/DIS pass, GE FY2022 fails."""

    def _break_flows(self, facts, amount, year):
        gaap = facts["facts"]["us-gaap"]
        for f in gaap["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"]:
            if f["end"].startswith(str(year)):
                f["val"] += amount

    def test_immaterial_residual_warns_and_quantifies(self, clean_facts):
        self._break_flows(clean_facts, 8 * M, 2023)   # 0.6% of FY2023 revenue
        report = validate_history(_map(clean_facts))
        h2 = report.result("H2")
        assert h2.status == "warn"
        assert h2.outcomes[2023] == "immaterial"
        assert h2.outcomes[2022] == "tie"             # other years unaffected
        assert h2.per_period[2023] == pytest.approx(8 * M)
        # dollars and both percentages stated, per affected year
        assert "IMMATERIAL" in h2.detail and "FY2023" in h2.detail
        assert "% of revenue" in h2.detail and "% of gross cash flows" in h2.detail
        assert report.overall == "pass_with_warnings"

    def test_material_residual_fails_with_magnitude(self, clean_facts):
        self._break_flows(clean_facts, 50 * M, 2023)  # ~3.8% of revenue
        report = validate_history(_map(clean_facts))
        h2 = report.result("H2")
        assert h2.status == "fail"
        assert h2.outcomes[2023] == "fail"
        assert "MATERIAL" in h2.detail and "exceeds the materiality" in h2.detail

    def test_flows_leg_binds_independently_of_revenue_leg(self, clean_facts):
        # Inflate revenue 100x: a 20M residual is far under 1% of revenue but
        # over 5% of gross flows — the secondary leg must still catch it.
        gaap = clean_facts["facts"]["us-gaap"]
        for tag in ("RevenueFromContractWithCustomerExcludingAssessedTax",
                    "Revenues"):
            for f in gaap.get(tag, {}).get("units", {}).get("USD", []):
                if f["end"].startswith("2023"):
                    f["val"] *= 100
        self._break_flows(clean_facts, 20 * M, 2023)
        report = validate_history(_map(clean_facts))
        assert report.result("H2").outcomes[2023] == "fail"

    def test_immaterial_years_surface_as_assembled_warning(self, clean_facts):
        # Owner requirement: visible in the assembled output, not buried in a log.
        self._break_flows(clean_facts, 8 * M, 2023)
        h = build_financial_history("SYN", make_source(clean_facts))
        w = [w for w in h.warnings if w.code == "immaterial_cash_residual"]
        assert len(w) == 1
        assert w[0].detail["years"] == [2023]
        assert w[0].detail["residuals"][2023] == pytest.approx(8 * M)
        assert "of revenue" in w[0].message and "FY2023" in w[0].message

    def test_definitional_and_immaterial_stay_distinguishable(self, clean_facts):
        # Broaden the cash definition (definitional, FY2021+) AND leave a small
        # unreconciled residual in FY2023: both outcomes must appear, separately.
        gaap = clean_facts["facts"]["us-gaap"]
        for tag in ("NetCashProvidedByUsedInOperatingActivities",
                    "CashAndCashEquivalentsPeriodIncreaseDecrease"):
            for f in gaap[tag]["units"]["USD"]:
                f["val"] += 10 * M
        gaap["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
             "IncludingDisposalGroupAndDiscontinuedOperations"] = {
            "units": {"USD": [
                inst(model_year(y)["cash"] + 10 * M * (y - 2019), y)
                for y in range(2020, 2025)]}}
        self._break_flows(clean_facts, 8 * M, 2023)
        h2 = validate_history(_map(clean_facts)).result("H2")
        assert h2.status == "warn"
        assert h2.outcomes[2022] == "definitional"
        assert h2.outcomes[2023] == "immaterial"
        assert "DEFINITIONAL" in h2.detail and "IMMATERIAL" in h2.detail


# ── Final round: coverage gate (owner-approved refuse <60%, warn 60–85%) ─────

def _mapped_with_coverage(assets_share, liabs_share):
    p = FiscalPeriod(fiscal_year=2024, start=date(2024, 1, 1),
                     end=date(2024, 12, 31), duration_days=365, is_53_week=False)
    p.balance["other_noncurrent_assets"] = Fact(
        value=84.9e9, unit="USD", tag="derived", source="derived")
    cov = Coverage(fiscal_year=2024, assets_named_share=assets_share,
                   liabilities_named_share=liabs_share,
                   expenses_named_share=None, revenue_named_share=1.0,
                   top_unmapped=[UnmappedTag(
                       tag="FinancingReceivableAfterAllowanceForCreditLoss",
                       value=45.0e9, shape="instant")])
    return MappedHistory(periods=[p], warnings=[], ni_pairs={}, alt_cash={},
                         coverage=cov)


class TestCoverageGate:
    """A filer that builds badly is more dangerous than one that fails —
    the gate turns silently-thin mappings into a refusal (or a hard warning)."""

    def test_refusal_below_floor_is_diagnostic(self):
        m = _mapped_with_coverage(0.20, 0.18)       # the DE shape
        with pytest.raises(InsufficientCoverageError) as exc:
            _coverage_gate("DE", m)
        msg = exc.value.user_message
        assert "20% of assets" in msg and "18% of liabilities" in msg
        assert "other_noncurrent_assets $84.9B" in msg     # residual bucket named
        assert "FinancingReceivableAfterAllowanceForCreditLoss" in msg
        assert exc.value.detail["floor"] == 0.60

    def test_gate_uses_min_of_assets_and_liabilities(self):
        with pytest.raises(InsufficientCoverageError):
            _coverage_gate("SYN", _mapped_with_coverage(1.00, 0.55))

    def test_middle_band_builds_behind_hard_warning(self):
        m = _mapped_with_coverage(0.73, 0.96)       # the NVDA shape
        _coverage_gate("NVDA", m)                   # must not raise
        assert [w.code for w in m.warnings] == ["coverage_low"]
        assert "73% of assets" in m.warnings[0].message
        assert m.warnings[0].detail["assets_named_share"] == 0.73

    def test_clean_coverage_passes_silently(self):
        m = _mapped_with_coverage(0.95, 0.94)
        _coverage_gate("SYN", m)
        assert m.warnings == []

    def test_synthetic_clean_filer_clears_the_gate(self, clean_facts):
        # End-to-end: the clean fixture must not trip the gate or the band.
        h = build_financial_history("SYN", make_source(clean_facts))
        assert not any(w.code == "coverage_low" for w in h.warnings)


# ── Diagnostic-pass fix (owner-approved 2026-08-14): expense coverage ────────

class TestExpenseCoverageIdentityGap:
    """The metric counted only DERIVED residuals, so a real-but-tiny tagged
    other_operating line masked MCD's ~$11.5B of untagged restaurant costs —
    it read E100% over the hole. A broken alarm is worse than no alarm: the
    fixed metric also counts the margin-identity gap (revenue − EBIT − Σ named
    cost lines) and must read LOW on MCD."""

    def test_would_have_caught_mcd(self):
        from test_fixtures_real import source_for
        h = build_financial_history("MCD", source_for("MCD"))
        share = h.coverage.expenses_named_share
        assert share is not None
        # the hole is ~80% of operating costs; anything reading above the
        # 60% refuse-floor analogue means the alarm is still broken
        assert share < 0.60, f"expense share {share:.0%} — MCD hole masked again"

    def test_clean_filer_still_reads_fully_covered(self, clean_facts):
        h = build_financial_history("SYN", make_source(clean_facts))
        assert h.coverage.expenses_named_share == pytest.approx(1.0)

    def test_derived_residual_still_counts_as_unmapped(self, clean_facts):
        # by-nature filer: the residual WE derive closes the identity, so the
        # gap term is zero — but the residual itself still counts, exactly as
        # before the fix (no double counting, no regression)
        gaap = clean_facts["facts"]["us-gaap"]
        del gaap["CostOfRevenue"]
        del gaap["GrossProfit"]
        m = _map(clean_facts)
        share = m.coverage.expenses_named_share
        y = model_year(2024)
        opex = y["revenue"] - y["oi"]
        assert share == pytest.approx(1.0 - y["cogs"] / opex)


# ── Final round: VZ capex chain add ──────────────────────────────────────────

def test_capex_chain_includes_vz_other_productive_assets():
    # PaymentsToAcquireOtherProductiveAssets, verified $17.0B FY2025 (VZ files
    # the standard tags only through 2018).
    assert "PaymentsToAcquireOtherProductiveAssets" in load_schema().items["capex"].tags
