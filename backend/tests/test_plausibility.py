"""Plausibility checks PL1–PL8: the clean synthetic filer stays quiet; each
perturbation trips its own rule (spec 07). Same approach as the H-check tests —
these target the misclassification failure class that arithmetic tie-outs
cannot see (the KHC long-term-debt gap balanced perfectly)."""

import pytest
from conftest import M, dur

from ingest.mapping import map_history
from ingest.schema import load_schema
from ingest.validation import validate_history


def _validate(facts):
    return validate_history(map_history(facts, load_schema(), "1231", "SYN"))


def _pl(report, n):
    return report.result(f"PL{n}")


def test_clean_filer_trips_no_plausibility_checks(clean_facts):
    report = _validate(clean_facts)
    for n in range(1, 9):
        assert _pl(report, n).status == "pass", f"PL{n} fired on a clean filer"
    assert all(r.severity == "warn" for r in report.results
               if r.check_id.startswith("PL"))


def _delete_debt(gaap):
    del gaap["LongTermDebtNoncurrent"]      # -> long_term_debt zero_logged, debt = 0


def test_pl1_interest_without_debt(clean_facts):
    gaap = clean_facts["facts"]["us-gaap"]
    _delete_debt(gaap)
    report = _validate(clean_facts)
    pl1 = _pl(report, 1)
    assert pl1.status == "warn"
    assert "interest" in pl1.detail and "FY2024" in pl1.detail
    assert pl1.per_period[2024] == pytest.approx(10 * M)
    # warnings never block: overall stays reviewable, not failed
    assert report.overall == "pass_with_warnings"


def test_pl2_da_without_asset_base(clean_facts):
    gaap = clean_facts["facts"]["us-gaap"]
    for tag in ("PropertyPlantAndEquipmentNet", "IntangibleAssetsNetExcludingGoodwill",
                "OperatingLeaseRightOfUseAsset"):
        del gaap[tag]
    report = _validate(clean_facts)
    assert _pl(report, 2).status == "warn"
    assert "PP&E" in _pl(report, 2).detail


def test_pl2_quiet_when_only_intangibles_amortize(clean_facts):
    # Asset-light filer: no PP&E but real intangibles -> D&A is explained.
    gaap = clean_facts["facts"]["us-gaap"]
    del gaap["PropertyPlantAndEquipmentNet"]
    report = _validate(clean_facts)
    assert _pl(report, 2).status == "pass"


def test_pl3_revenue_with_zero_cogs(clean_facts):
    gaap = clean_facts["facts"]["us-gaap"]
    for f in gaap["CostOfRevenue"]["units"]["USD"]:
        f["val"] = 0.0
    report = _validate(clean_facts)
    assert _pl(report, 3).status == "warn"


def test_pl4_debt_flows_without_debt(clean_facts):
    gaap = clean_facts["facts"]["us-gaap"]
    _delete_debt(gaap)
    gaap["RepaymentsOfLongTermDebt"] = {
        "units": {"USD": [dur(30 * M, y) for y in range(2020, 2025)]}}
    report = _validate(clean_facts)
    pl4 = _pl(report, 4)
    assert pl4.status == "warn"
    assert pl4.per_period[2024] == pytest.approx(30 * M)


def test_pl4_quiet_when_debt_exists(clean_facts):
    gaap = clean_facts["facts"]["us-gaap"]
    gaap["RepaymentsOfLongTermDebt"] = {
        "units": {"USD": [dur(30 * M, y) for y in range(2020, 2025)]}}
    report = _validate(clean_facts)
    assert _pl(report, 4).status == "pass"       # debt on BS explains the flows


def test_pl5_lease_cost_without_lease_balances(clean_facts):
    gaap = clean_facts["facts"]["us-gaap"]
    for tag in ("OperatingLeaseLiabilityCurrent", "OperatingLeaseLiabilityNoncurrent",
                "OperatingLeaseRightOfUseAsset"):
        del gaap[tag]
    gaap["OperatingLeaseCost"] = {
        "units": {"USD": [dur(15 * M, y) for y in range(2020, 2025)]}}
    report = _validate(clean_facts)
    pl5 = _pl(report, 5)
    assert pl5.status == "warn"
    assert "lease" in pl5.detail


def test_pl6_tax_expense_without_tax_balances(clean_facts):
    del clean_facts["facts"]["us-gaap"]["DeferredIncomeTaxLiabilitiesNet"]
    report = _validate(clean_facts)
    assert _pl(report, 6).status == "warn"


def test_pl6_quiet_when_taxes_payable_probe_hits(clean_facts):
    from conftest import inst
    gaap = clean_facts["facts"]["us-gaap"]
    del gaap["DeferredIncomeTaxLiabilitiesNet"]
    gaap["AccruedIncomeTaxesCurrent"] = {
        "units": {"USD": [inst(8 * M, y) for y in range(2020, 2025)]}}
    report = _validate(clean_facts)
    assert _pl(report, 6).status == "pass"


def test_pl7_lease_liability_without_rou(clean_facts):
    del clean_facts["facts"]["us-gaap"]["OperatingLeaseRightOfUseAsset"]
    report = _validate(clean_facts)
    pl7 = _pl(report, 7)
    assert pl7.status == "warn"
    assert "counterpart" in pl7.detail


def test_pl8_revenue_without_receivables(clean_facts):
    del clean_facts["facts"]["us-gaap"]["AccountsReceivableNetCurrent"]
    report = _validate(clean_facts)
    assert _pl(report, 8).status == "warn"


def test_plausibility_never_fails_overall(clean_facts):
    # Even with several rules tripped at once, PL checks are warnings by design.
    gaap = clean_facts["facts"]["us-gaap"]
    _delete_debt(gaap)
    del gaap["AccountsReceivableNetCurrent"]
    del gaap["DeferredIncomeTaxLiabilitiesNet"]
    report = _validate(clean_facts)
    assert report.overall == "pass_with_warnings"
