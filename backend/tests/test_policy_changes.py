"""Owner-approved policy changes from the bulk-scan review (items 2–8):
cost-structure classification, derived EBIT, window trim, unsplit investments,
dual-class share derivation, H2 definitional mismatches, split labeling, and
the known-unsupported list."""

import pytest
from conftest import M, dur, inst, make_source, model_year

from ingest.assemble import build_financial_history
from ingest.errors import KnownUnsupportedError, MissingRequiredItemError
from ingest.mapping import map_history
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
            f["val"] += 10 * M      # flows tie to NOTHING: not ncc, not any Δcash
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


# ── XOM: known-unsupported list ──────────────────────────────────────────────

def test_known_unsupported_returns_honest_message():
    with pytest.raises(KnownUnsupportedError) as exc:
        build_financial_history("XOM", make_source())
    assert "custom extension tags" in exc.value.user_message
    assert exc.value.detail["ticker"] == "XOM"
