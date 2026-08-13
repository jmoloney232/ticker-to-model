"""Shared fixtures: a synthetic company whose statements tie exactly by
construction, so each validation check can be broken one perturbation at a time.

Design (values in $, scaled 1e6): revenue grows $100M/yr; margins fixed; cash is
seeded and rolled by the cash flow statement; equity rolls by NI − dividends +
SBC (credited to paid-in capital); PP&E rolls by capex − D&A. Every identity
H1–H5 holds exactly. FY2020–FY2024, calendar fiscal years.

The revenue chain is deliberately split across tags: FY2020–21 use the legacy
`Revenues` tag, FY2022+ the ASC-606 tag — exercising per-year chain fallback.
"""

from __future__ import annotations

import pytest

from ingest.edgar import StaticSource

M = 1_000_000.0
CIK = 1234567
YEARS = list(range(2020, 2025))


def dur(val: float, year: int, *, form: str = "10-K", accn: str | None = None,
        filed: str | None = None, start: str | None = None, end: str | None = None) -> dict:
    return {
        "val": val,
        "start": start or f"{year}-01-01",
        "end": end or f"{year}-12-31",
        "accn": accn or f"acc-{year + 1}",
        "filed": filed or f"{year + 1}-02-15",
        "form": form,
        "fy": year, "fp": "FY",
    }


def inst(val: float, year: int, *, form: str = "10-K", accn: str | None = None,
         filed: str | None = None, end: str | None = None) -> dict:
    return {
        "val": val,
        "end": end or f"{year}-12-31",
        "accn": accn or f"acc-{year + 1}",
        "filed": filed or f"{year + 1}-02-15",
        "form": form,
        "fy": year, "fp": "FY",
    }


def model_year(year: int) -> dict[str, float]:
    """Closed-form statement values for one fiscal year (all in dollars)."""
    i = year - 2020
    revenue = (1000 + 100 * i) * M
    cogs = (600 + 50 * i) * M
    gross = revenue - cogs
    rnd, sga = 50 * M, 150 * M
    oi = gross - rnd - sga
    int_exp, int_inc = 10 * M, 5 * M
    pretax = oi - int_exp + int_inc
    tax = 0.25 * pretax
    ni = pretax - tax

    da, sbc, capex, dividends = 40 * M, 12 * M, 50 * M, 20 * M
    ar = (100 + 10 * i) * M
    inv = (50 + 5 * i) * M
    ap = (48 + 4 * i) * M
    wc_impact = 0.0 if i == 0 else -(10 * M + 5 * M) + 4 * M   # ΔAR+Δinv out, ΔAP in
    cfo = ni + da + sbc + wc_impact if i > 0 else ni + da + sbc
    cfi, cff = -capex, -dividends
    net_change = cfo + cfi + cff

    cash = 500 * M
    re = 200 * M
    contributed = 577 * M
    for y in range(2021, year + 1):
        prev = model_year_flows(y)
        cash += prev["net_change"]
        re += prev["ni"] - dividends
        contributed += sbc

    ppe = (300 + 10 * i) * M
    oca, goodwill, intang, onca = 25 * M, 100 * M, 50 * M, 30 * M
    tca = cash + ar + inv + oca
    assets = tca + ppe + goodwill + intang + onca

    accrued, oll_cur, ocl = 30 * M, 10 * M, 20 * M
    tcl = ap + accrued + oll_cur + ocl
    ltd, oll_non, dtl, oncl = 200 * M, 30 * M, 15 * M, 25 * M
    tl = tcl + ltd + oll_non + dtl + oncl
    se = contributed + re

    return {
        "revenue": revenue, "cogs": cogs, "gross": gross, "rnd": rnd, "sga": sga,
        "oi": oi, "int_exp": int_exp, "int_inc": int_inc, "pretax": pretax,
        "tax": tax, "ni": ni,
        "da": da, "sbc": sbc, "capex": capex, "dividends": dividends,
        "cfo": cfo, "cfi": cfi, "cff": cff, "net_change": net_change,
        "cash": cash, "ar": ar, "inv": inv, "oca": oca, "tca": tca, "ppe": ppe,
        "goodwill": goodwill, "intang": intang, "onca": onca, "assets": assets,
        "ap": ap, "accrued": accrued, "oll_cur": oll_cur, "ocl": ocl, "tcl": tcl,
        "ltd": ltd, "oll_non": oll_non, "dtl": dtl, "oncl": oncl, "tl": tl,
        "re": re, "se": se, "le": tl + se,
    }


def model_year_flows(year: int) -> dict[str, float]:
    i = year - 2020
    pretax = ((1000 + 100 * i) - (600 + 50 * i) - 200 - 10 + 5) * M
    ni = 0.75 * pretax
    wc_impact = -(15 * M) + 4 * M if i > 0 else 0.0
    cfo = ni + 40 * M + 12 * M + wc_impact
    return {"ni": ni, "net_change": cfo - 50 * M - 20 * M}


def build_companyfacts(years: list[int] = YEARS, revenue_unit: str = "USD") -> dict:
    gaap: dict[str, dict] = {}

    def add_dur(tag: str, year: int, val: float, unit: str = "USD") -> None:
        gaap.setdefault(tag, {"units": {}})["units"].setdefault(unit, []).append(dur(val, year))

    def add_inst(tag: str, year: int, val: float, unit: str = "USD") -> None:
        gaap.setdefault(tag, {"units": {}})["units"].setdefault(unit, []).append(inst(val, year))

    for y in years:
        v = model_year(y)
        # income statement — revenue tag switches at FY2022 (chain fallback test)
        rev_tag = ("RevenueFromContractWithCustomerExcludingAssessedTax" if y >= 2022
                   else "Revenues")
        add_dur(rev_tag, y, v["revenue"], revenue_unit)
        add_dur("CostOfRevenue", y, v["cogs"])
        add_dur("GrossProfit", y, v["gross"])
        add_dur("ResearchAndDevelopmentExpense", y, v["rnd"])
        add_dur("SellingGeneralAndAdministrativeExpense", y, v["sga"])
        add_dur("OperatingIncomeLoss", y, v["oi"])
        add_dur("InterestExpense", y, v["int_exp"])
        add_dur("InvestmentIncomeInterest", y, v["int_inc"])
        add_dur("IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
                "ExtraordinaryItemsNoncontrollingInterest", y, v["pretax"])
        add_dur("IncomeTaxExpenseBenefit", y, v["tax"])
        add_dur("NetIncomeLoss", y, v["ni"])
        add_dur("WeightedAverageNumberOfSharesOutstandingBasic", y, 100 * M, "shares")
        add_dur("WeightedAverageNumberOfDilutedSharesOutstanding", y, 102 * M, "shares")
        # balance sheet
        add_inst("CashAndCashEquivalentsAtCarryingValue", y, v["cash"])
        add_inst("AccountsReceivableNetCurrent", y, v["ar"])
        add_inst("InventoryNet", y, v["inv"])
        add_inst("OtherAssetsCurrent", y, v["oca"])
        add_inst("AssetsCurrent", y, v["tca"])
        add_inst("PropertyPlantAndEquipmentNet", y, v["ppe"])
        add_inst("Goodwill", y, v["goodwill"])
        add_inst("IntangibleAssetsNetExcludingGoodwill", y, v["intang"])
        add_inst("OtherAssetsNoncurrent", y, v["onca"])
        add_inst("Assets", y, v["assets"])
        add_inst("AccountsPayableCurrent", y, v["ap"])
        add_inst("AccruedLiabilitiesCurrent", y, v["accrued"])
        add_inst("OperatingLeaseLiabilityCurrent", y, v["oll_cur"])
        add_inst("OtherLiabilitiesCurrent", y, v["ocl"])
        add_inst("LiabilitiesCurrent", y, v["tcl"])
        add_inst("LongTermDebtNoncurrent", y, v["ltd"])
        add_inst("OperatingLeaseLiabilityNoncurrent", y, v["oll_non"])
        add_inst("DeferredIncomeTaxLiabilitiesNet", y, v["dtl"])
        add_inst("OtherLiabilitiesNoncurrent", y, v["oncl"])
        add_inst("Liabilities", y, v["tl"])
        add_inst("StockholdersEquity", y, v["se"])
        add_inst("RetainedEarningsAccumulatedDeficit", y, v["re"])
        add_inst("LiabilitiesAndStockholdersEquity", y, v["le"])
        # cash flow
        add_dur("DepreciationDepletionAndAmortization", y, v["da"])
        add_dur("ShareBasedCompensation", y, v["sbc"])
        add_dur("NetCashProvidedByUsedInOperatingActivities", y, v["cfo"])
        add_dur("PaymentsToAcquirePropertyPlantAndEquipment", y, v["capex"])
        add_dur("NetCashProvidedByUsedInInvestingActivities", y, v["cfi"])
        add_dur("PaymentsOfDividends", y, v["dividends"])
        add_dur("NetCashProvidedByUsedInFinancingActivities", y, v["cff"])
        add_dur("EffectOfExchangeRateOnCashAndCashEquivalents", y, 0.0)
        add_dur("CashAndCashEquivalentsPeriodIncreaseDecrease", y, v["net_change"])

    dei = {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        *[inst(100 * M, y, form="10-K") for y in years],
        inst(99 * M, years[-1], form="10-Q", end=f"{years[-1] + 1}-04-25",
             filed=f"{years[-1] + 1}-04-30", accn=f"acc-q-{years[-1] + 1}"),
    ]}}}

    return {"cik": CIK, "entityName": "Synthetic Co",
            "facts": {"us-gaap": gaap, "dei": dei}}


def make_source(facts: dict | None = None, sic: str = "7372") -> StaticSource:
    return StaticSource(
        tickers={"0": {"cik_str": CIK, "ticker": "SYN", "title": "Synthetic Co"}},
        submissions={"cik": str(CIK), "name": "Synthetic Co", "sic": sic,
                     "sicDescription": "Prepackaged software", "fiscalYearEnd": "1231"},
        companyfacts=facts if facts is not None else build_companyfacts(),
    )


@pytest.fixture
def clean_facts() -> dict:
    return build_companyfacts()


@pytest.fixture
def clean_source(clean_facts) -> StaticSource:
    return make_source(clean_facts)
