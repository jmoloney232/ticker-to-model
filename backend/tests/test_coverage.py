"""Coverage metric (spec 01, Outputs): named shares fall exactly by what drops
into a derived residual bucket, and unconsumed tags surface by magnitude."""

import pytest
from conftest import M, inst, model_year

from ingest.mapping import map_history
from ingest.schema import load_schema


def _coverage(facts):
    return map_history(facts, load_schema(), "1231", "SYN").coverage


def test_clean_filer_is_fully_named(clean_facts):
    cov = _coverage(clean_facts)
    assert cov.fiscal_year == 2024
    assert cov.assets_named_share == pytest.approx(1.0)
    assert cov.liabilities_named_share == pytest.approx(1.0)
    assert cov.expenses_named_share == pytest.approx(1.0)
    assert cov.revenue_named_share == 1.0
    # everything the synthetic filer tags is consumed by a chain
    assert cov.top_unmapped == []


def test_share_falls_by_exactly_the_derived_residual(clean_facts):
    # Untag the filer's own "other noncurrent assets" -> the mapper derives a
    # residual of the same size, and the named share drops by residual/total.
    del clean_facts["facts"]["us-gaap"]["OtherAssetsNoncurrent"]
    cov = _coverage(clean_facts)
    v = model_year(2024)
    assert cov.assets_named_share == pytest.approx(1.0 - v["onca"] / v["assets"])
    assert cov.liabilities_named_share == pytest.approx(1.0)


def test_liability_share_reacts_to_untagged_other_liabilities(clean_facts):
    del clean_facts["facts"]["us-gaap"]["OtherLiabilitiesCurrent"]
    cov = _coverage(clean_facts)
    v = model_year(2024)
    assert cov.liabilities_named_share == pytest.approx(1.0 - v["ocl"] / v["tl"])


def test_unconsumed_tags_surface_by_magnitude(clean_facts):
    gaap = clean_facts["facts"]["us-gaap"]
    gaap["DueFromRelatedPartiesCurrent"] = {
        "units": {"USD": [inst(500 * M, y) for y in range(2020, 2025)]}}
    gaap["RestrictedCashNoncurrent"] = {
        "units": {"USD": [inst(90 * M, y) for y in range(2020, 2025)]}}
    cov = _coverage(clean_facts)
    tags = [u.tag for u in cov.top_unmapped]
    assert tags[0] == "DueFromRelatedPartiesCurrent"      # biggest first
    assert "RestrictedCashNoncurrent" in tags
    assert cov.top_unmapped[0].value == pytest.approx(500 * M)
    assert cov.top_unmapped[0].shape == "instant"


def test_coverage_rides_on_the_assembled_history(clean_source):
    from ingest.assemble import build_financial_history
    h = build_financial_history("SYN", clean_source)
    assert h.coverage is not None
    assert h.coverage.assets_named_share == pytest.approx(1.0)
