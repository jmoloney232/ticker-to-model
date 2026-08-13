"""Tag mapping, chain fallback, missing rules, and derivers (spec 01 §5)."""

import pytest
from conftest import M, build_companyfacts, dur, inst, model_year

from ingest.errors import MissingRequiredItemError
from ingest.mapping import map_history
from ingest.schema import load_schema


def _map(facts, years=5):
    return map_history(facts, load_schema(), "1231", "SYN", years=years)


def test_five_gapless_periods_assembled(clean_facts):
    m = _map(clean_facts)
    assert [p.fiscal_year for p in m.periods] == [2020, 2021, 2022, 2023, 2024]
    assert all(not p.is_53_week for p in m.periods)


def test_chain_fallback_picks_per_year_winner(clean_facts):
    """FY2020–21 filed under `Revenues`, FY2022+ under the ASC-606 tag."""
    m = _map(clean_facts)
    by_fy = {p.fiscal_year: p.income["revenue"] for p in m.periods}
    assert by_fy[2021].tag == "us-gaap:Revenues"
    assert by_fy[2023].tag == ("us-gaap:RevenueFromContractWithCustomer"
                               "ExcludingAssessedTax")
    assert by_fy[2023].value == model_year(2023)["revenue"]


def test_required_item_missing_raises(clean_facts):
    for tag in ("IncomeTaxExpenseBenefit",):
        del clean_facts["facts"]["us-gaap"][tag]
    with pytest.raises(MissingRequiredItemError) as exc:
        _map(clean_facts)
    assert exc.value.detail["item"] == "income_tax"
    assert "IncomeTaxExpenseBenefit" in exc.value.detail["tags_tried"]


def test_optional_missing_becomes_logged_zero_never_silent(clean_facts):
    m = _map(clean_facts)
    p = m.periods[-1]
    sti = p.balance["short_term_investments"]      # no tag in the synthetic filer
    assert sti.value == 0.0 and sti.source == "zero_logged"
    assert any(w.code == "unmapped_item" and w.item == "short_term_investments"
               for w in m.warnings)


def test_omit_items_stay_absent(clean_facts):
    m = _map(clean_facts)
    assert "eps_basic" not in m.periods[-1].income   # missing_rule: omit


def test_residual_derivers_reconcile_exactly(clean_facts):
    m = _map(clean_facts)
    p = m.periods[-1]
    v = model_year(2024)
    # tagged when the filer reports them (tag beats derivation) ...
    assert p.balance["other_current_liabilities"].value == pytest.approx(v["ocl"])
    assert p.balance["other_current_liabilities"].source == "tag"
    assert p.balance["other_noncurrent_liabilities"].value == pytest.approx(v["oncl"])
    assert p.balance["other_noncurrent_assets"].value == pytest.approx(v["onca"])
    # ... and the pure residuals (never tagged here) reconcile the statements exactly
    assert p.income["other_operating"].value == pytest.approx(0.0)
    assert p.income["other_operating"].source == "derived"
    assert p.income["other_nonoperating"].value == pytest.approx(0.0)


def test_residual_deriver_reconstructs_untagged_other_liabilities(clean_facts):
    del clean_facts["facts"]["us-gaap"]["OtherLiabilitiesCurrent"]
    m = _map(clean_facts)
    p = m.periods[-1]
    ocl = p.balance["other_current_liabilities"]
    assert ocl.source == "derived"
    assert ocl.value == pytest.approx(model_year(2024)["ocl"])


def test_operating_lease_combined_from_split_tags(clean_facts):
    m = _map(clean_facts)
    p = m.periods[-1]
    assert p.balance["operating_lease_liability"].value == pytest.approx(40 * M)
    assert p.balance["operating_lease_liability"].source == "derived"


def test_working_capital_residual(clean_facts):
    m = _map(clean_facts)
    p = m.periods[-1]
    assert p.cashflow["working_capital_change"].value == pytest.approx(-11 * M)


def test_working_capital_tag_sign_negated(clean_facts):
    # IncreaseDecreaseInOperatingCapital positive = WC build = cash OUTflow.
    gaap = clean_facts["facts"]["us-gaap"]
    gaap["IncreaseDecreaseInOperatingCapital"] = {
        "units": {"USD": [dur(11 * M, y) for y in range(2020, 2025)]}}
    m = _map(clean_facts)
    assert m.periods[-1].cashflow["working_capital_change"].value == pytest.approx(-11 * M)
    assert m.periods[-1].cashflow["working_capital_change"].tag.endswith(
        "IncreaseDecreaseInOperatingCapital")


def test_combined_ap_accrued_tag_forces_accrued_to_zero():
    facts = build_companyfacts()
    gaap = facts["facts"]["us-gaap"]
    del gaap["AccountsPayableCurrent"]
    del gaap["AccruedLiabilitiesCurrent"]
    gaap["AccountsPayableAndAccruedLiabilitiesCurrent"] = {"units": {"USD": [
        inst(model_year(y)["ap"] + model_year(y)["accrued"], y)
        for y in range(2020, 2025)]}}
    m = _map(facts)
    p = m.periods[-1]
    assert p.balance["accounts_payable"].value == pytest.approx(
        model_year(2024)["ap"] + model_year(2024)["accrued"])
    assert p.balance["accrued_liabilities"].value == 0.0
    assert any(w.code == "combined_ap_accrued" for w in m.warnings)


def test_net_income_via_profitloss_subtracts_nci():
    facts = build_companyfacts()
    gaap = facts["facts"]["us-gaap"]
    del gaap["NetIncomeLoss"]
    for y in range(2020, 2025):
        ni = model_year(y)["ni"]
        gaap.setdefault("ProfitLoss", {"units": {"USD": []}})["units"]["USD"].append(
            dur(ni + 7 * M, y))
        gaap.setdefault("NetIncomeLossAttributableToNoncontrollingInterest",
                        {"units": {"USD": []}})["units"]["USD"].append(dur(7 * M, y))
    m = _map(facts)
    p = m.periods[-1]
    assert p.income["net_income"].value == pytest.approx(model_year(2024)["ni"])
    assert p.income["net_income"].tag == "us-gaap:ProfitLoss"


def test_gap_in_history_limits_run_and_names_gap():
    from ingest.errors import InsufficientHistoryError
    facts = build_companyfacts(years=[2019, 2020, 2021, 2022, 2023, 2024])
    gaap = facts["facts"]["us-gaap"]
    for tag in ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"):
        if tag in gaap:
            gaap[tag]["units"]["USD"] = [
                f for f in gaap[tag]["units"]["USD"] if not f["end"].startswith("2022")]
    with pytest.raises(InsufficientHistoryError) as exc:
        _map(facts)
    assert "gap" in exc.value.user_message.lower()
    assert exc.value.detail["found"] == 2      # only FY2023–24 are consecutive


def test_gap_with_enough_recent_years_trims_and_warns():
    facts = build_companyfacts(years=[2018, 2019, 2020, 2022, 2023, 2024])
    m = _map(facts)   # FY2021 missing entirely -> recent gapless run is 2022–24
    assert [p.fiscal_year for p in m.periods] == [2022, 2023, 2024]
    assert any(w.code == "history_trimmed_at_gap" and "FY2021" in w.message
               for w in m.warnings)


def test_provenance_on_every_fact(clean_facts):
    m = _map(clean_facts)
    for p in m.periods:
        for stmt in (p.income, p.balance, p.cashflow):
            for f in stmt.values():
                assert f.source in ("tag", "derived", "zero_logged")
                if f.source == "tag":
                    assert f.accession and f.filed is not None
