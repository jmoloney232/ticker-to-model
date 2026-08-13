"""End-to-end assembly through StaticSource (spec 01, Pipeline + Error cases)."""

import pytest
from conftest import M, build_companyfacts, make_source

from ingest.assemble import build_financial_history
from ingest.errors import (
    FinancialCompanyError,
    UnknownTickerError,
    UnsupportedCurrencyError,
    ValidationFailedError,
)


def test_happy_path(clean_source):
    h = build_financial_history("syn", clean_source)   # case-insensitive
    assert h.company.name == "Synthetic Co"
    assert [p.fiscal_year for p in h.periods] == [2020, 2021, 2022, 2023, 2024]
    assert h.validation.overall == "pass"
    assert h.staleness == {"submissions": "live", "companyfacts": "live"}
    # current cover-page shares come from the later 10-Q, not the 10-K
    assert h.shares_current.value == 99 * M
    assert h.shares_current.tag == "dei:EntityCommonStockSharesOutstanding"


def test_bank_rejected_cleanly():
    source = make_source(sic="6021")
    with pytest.raises(FinancialCompanyError) as exc:
        build_financial_history("SYN", source)
    assert "bank" in exc.value.user_message
    assert exc.value.detail["sic"] == 6021


@pytest.mark.parametrize("sic,category", [
    ("6311", "insurance"), ("6798", "REIT"), ("6726", "investment fund")])
def test_other_financials_rejected(sic, category):
    with pytest.raises(FinancialCompanyError) as exc:
        build_financial_history("SYN", make_source(sic=sic))
    assert category.lower() in exc.value.user_message.lower()


def test_unknown_ticker():
    with pytest.raises(UnknownTickerError):
        build_financial_history("NOPE", make_source())


def test_non_usd_reporter_rejected():
    facts = build_companyfacts(revenue_unit="EUR")
    with pytest.raises(UnsupportedCurrencyError) as exc:
        build_financial_history("SYN", make_source(facts))
    assert "EUR" in exc.value.user_message


def test_untied_statements_refuse_to_build():
    facts = build_companyfacts()
    for f in facts["facts"]["us-gaap"]["Assets"]["units"]["USD"]:
        f["val"] += 500 * M
    with pytest.raises(ValidationFailedError) as exc:
        build_financial_history("SYN", make_source(facts))
    assert "H1" in exc.value.user_message
    assert exc.value.report.result("H1").status == "fail"


def test_warnings_flow_through(clean_source):
    h = build_financial_history("SYN", clean_source)
    codes = {w.code for w in h.warnings}
    assert "unmapped_item" in codes          # optional items the filer never tags
    assert "restated" not in codes           # clean filer
