"""Validation gate H1–H7: clean history passes; each perturbation trips exactly
its own check (spec 07, How tested)."""

import pytest
from conftest import M, build_companyfacts, dur

from ingest.mapping import map_history
from ingest.schema import load_schema
from ingest.validation import validate_history


def _validate(facts):
    return validate_history(map_history(facts, load_schema(), "1231", "SYN"))


def _statuses(report):
    return {r.check_id: r.status for r in report.results}


def test_clean_history_passes_everything(clean_facts):
    report = _validate(clean_facts)
    s = _statuses(report)
    assert s["H1"] == "pass"
    assert s["H2"] == "pass"
    assert s["H3"] == "skipped"     # ProfitLoss never co-reported in the synthetic filer
    assert s["H4"] == "pass"
    assert s["H5"] == "pass"
    assert s["H6"] == "pass"
    assert s["H7"] == "pass"
    assert report.overall == "pass"


def test_h1_catches_unbalanced_balance_sheet(clean_facts):
    gaap = clean_facts["facts"]["us-gaap"]
    for f in gaap["Assets"]["units"]["USD"]:
        if f["end"] == "2023-12-31":
            f["val"] += 50 * M
    report = _validate(clean_facts)
    h1 = report.result("H1")
    assert h1.status == "fail"
    assert h1.per_period[2023] == pytest.approx(50 * M)
    assert report.overall == "fail"
    # collateral H5 noise is fine; the point is H2/H4 don't fire
    assert report.result("H2").status == "pass"


def test_h2_catches_cash_flow_that_does_not_tie(clean_facts):
    gaap = clean_facts["facts"]["us-gaap"]
    for f in gaap["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"]:
        if f["end"] == "2023-12-31":
            f["val"] += 50 * M
    report = _validate(clean_facts)
    assert report.result("H2").status == "fail"
    assert report.result("H1").status == "pass"
    assert report.overall == "fail"


def test_h3_catches_net_income_disagreement(clean_facts):
    gaap = clean_facts["facts"]["us-gaap"]
    ni_facts = gaap["NetIncomeLoss"]["units"]["USD"]
    gaap["ProfitLoss"] = {"units": {"USD": [dict(f, val=f["val"] + 30 * M)
                                            for f in ni_facts]}}
    report = _validate(clean_facts)
    assert report.result("H3").status == "fail"
    assert report.overall == "fail"


def test_h4_warns_on_retained_earnings_break(clean_facts):
    gaap = clean_facts["facts"]["us-gaap"]
    for f in gaap["RetainedEarningsAccumulatedDeficit"]["units"]["USD"]:
        if f["end"] == "2023-12-31":
            f["val"] += 100 * M
    report = _validate(clean_facts)
    assert report.result("H4").status == "warn"
    assert report.result("H1").status == "pass"   # SE tag untouched — soft check only
    assert report.overall == "pass_with_warnings"


def test_h4_skips_when_retained_earnings_unreported(clean_facts):
    del clean_facts["facts"]["us-gaap"]["RetainedEarningsAccumulatedDeficit"]
    report = _validate(clean_facts)
    assert report.result("H4").status == "skipped"


def test_h5_warns_on_reported_vs_derived_mismatch(clean_facts):
    gaap = clean_facts["facts"]["us-gaap"]
    for f in gaap["GrossProfit"]["units"]["USD"]:
        if f["end"] == "2023-12-31":
            f["val"] += 20 * M
    report = _validate(clean_facts)
    h5 = report.result("H5")
    assert h5.status == "warn"
    assert "gross profit" in h5.detail


def test_h6_surfaces_restatements(clean_facts):
    gaap = clean_facts["facts"]["us-gaap"]
    gaap["Revenues"]["units"]["USD"].append(
        dur(1100 * M + 50 * M, 2021, accn="acc-2024-restate", filed="2024-02-15"))
    report = _validate(clean_facts)
    h6 = report.result("H6")
    assert h6.status == "warn"
    assert h6.per_period[2021] == pytest.approx(50 / 1100, rel=1e-6)
    assert report.overall == "pass_with_warnings"


def test_h7_flags_53_week_year_as_info_only():
    facts = build_companyfacts(years=[2020, 2021, 2022, 2024])
    gaap = facts["facts"]["us-gaap"]
    # FY2023 rebuilt as a 53-week year (371 days) for every duration tag
    from conftest import model_year
    v = model_year(2023)
    for tag, val in [
        ("RevenueFromContractWithCustomerExcludingAssessedTax", v["revenue"]),
        ("CostOfRevenue", v["cogs"]), ("GrossProfit", v["gross"]),
        ("ResearchAndDevelopmentExpense", v["rnd"]),
        ("SellingGeneralAndAdministrativeExpense", v["sga"]),
        ("OperatingIncomeLoss", v["oi"]), ("InterestExpense", v["int_exp"]),
        ("InvestmentIncomeInterest", v["int_inc"]),
        (("IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
          "ExtraordinaryItemsNoncontrollingInterest"), v["pretax"]),
        ("IncomeTaxExpenseBenefit", v["tax"]), ("NetIncomeLoss", v["ni"]),
        ("DepreciationDepletionAndAmortization", v["da"]),
        ("ShareBasedCompensation", v["sbc"]),
        ("NetCashProvidedByUsedInOperatingActivities", v["cfo"]),
        ("PaymentsToAcquirePropertyPlantAndEquipment", v["capex"]),
        ("NetCashProvidedByUsedInInvestingActivities", v["cfi"]),
        ("PaymentsOfDividends", v["dividends"]),
        ("NetCashProvidedByUsedInFinancingActivities", v["cff"]),
        ("EffectOfExchangeRateOnCashAndCashEquivalents", 0.0),
        ("CashAndCashEquivalentsPeriodIncreaseDecrease", v["net_change"]),
    ]:
        gaap.setdefault(tag, {"units": {"USD": []}})["units"]["USD"].append(
            dur(val, 2023, start="2022-12-26"))
    for tag in ("WeightedAverageNumberOfSharesOutstandingBasic",
                "WeightedAverageNumberOfDilutedSharesOutstanding"):
        gaap[tag]["units"]["shares"].append(
            dur(100 * M if "Basic" in tag else 102 * M, 2023, start="2022-12-26"))
    from conftest import inst
    for tag, key in [
        ("CashAndCashEquivalentsAtCarryingValue", "cash"),
        ("AccountsReceivableNetCurrent", "ar"), ("InventoryNet", "inv"),
        ("OtherAssetsCurrent", "oca"), ("AssetsCurrent", "tca"),
        ("PropertyPlantAndEquipmentNet", "ppe"), ("Goodwill", "goodwill"),
        ("IntangibleAssetsNetExcludingGoodwill", "intang"),
        ("OperatingLeaseRightOfUseAsset", "rou"),
        ("OtherAssetsNoncurrent", "onca"), ("Assets", "assets"),
        ("AccountsPayableCurrent", "ap"), ("AccruedLiabilitiesCurrent", "accrued"),
        ("OperatingLeaseLiabilityCurrent", "oll_cur"),
        ("OtherLiabilitiesCurrent", "ocl"), ("LiabilitiesCurrent", "tcl"),
        ("LongTermDebtNoncurrent", "ltd"),
        ("OperatingLeaseLiabilityNoncurrent", "oll_non"),
        ("DeferredIncomeTaxLiabilitiesNet", "dtl"),
        ("OtherLiabilitiesNoncurrent", "oncl"), ("Liabilities", "tl"),
        ("StockholdersEquity", "se"),
        ("RetainedEarningsAccumulatedDeficit", "re"),
        ("LiabilitiesAndStockholdersEquity", "le"),
    ]:
        gaap[tag]["units"]["USD"].append(inst(v[key], 2023))

    report = _validate(facts)
    h7 = report.result("H7")
    assert h7.status == "warn" and h7.severity == "info"
    assert "FY2023" in h7.detail
    assert report.overall in ("pass", "pass_with_warnings")


def test_report_always_contains_every_check(clean_facts):
    report = _validate(clean_facts)
    assert [r.check_id for r in report.results] == (
        [f"H{i}" for i in range(1, 8)] + [f"PL{i}" for i in range(1, 9)])
