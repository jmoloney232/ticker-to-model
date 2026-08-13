"""Schema-file structural tests (spec 02, How tested)."""

from ingest.mapping import DERIVERS
from ingest.schema import load_schema


def test_schema_loads_and_validates():
    schema = load_schema()
    assert len(schema.items) >= 60
    assert {"income", "balance", "cashflow"} == {i.statement for i in schema.items.values()}


def test_every_derive_item_has_an_executable_deriver():
    """schema.yaml derive strings are docs; DERIVERS is the executable set.
    They must name exactly the same items or the schema is lying."""
    schema = load_schema()
    declared = {i.name for i in schema.items.values() if i.missing_rule == "derive"}
    executable = {name for name, _ in DERIVERS}
    assert declared == executable


def test_required_items_are_the_expected_backbone():
    # cost_of_revenue and operating_income became optional by owner decision
    # (bulk scan items 4 and 5): by-nature filers have no COGS concept, and
    # some filers omit the EBIT subtotal in some years.
    schema = load_schema()
    required = {i.name for i in schema.items.values() if i.required}
    backbone = {"revenue", "pretax_income",
                "income_tax", "net_income", "shares_basic_wa", "cash_and_equivalents",
                "total_assets", "stockholders_equity", "shares_outstanding",
                "d_and_a", "cash_from_operations", "capex", "cash_from_investing",
                "cash_from_financing"}
    assert required == backbone


def test_namespaces_resolve():
    schema = load_schema()
    shares = schema.items["shares_outstanding"]
    assert shares.namespaced_tags() == [
        ("dei", "EntityCommonStockSharesOutstanding"),
        ("us-gaap", "CommonStockSharesOutstanding"),   # GOOGL dual-class fallback
    ]
    assert shares.selection == "latest"
    rev = schema.items["revenue"]
    assert rev.namespaced_tags()[0] == (
        "us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax")
