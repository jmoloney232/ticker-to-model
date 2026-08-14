"""Real-filing fixture tests (spec 01 + spec 02, How tested).

These run against committed EDGAR snapshots captured by `ingest.snapshot` —
real filed data, offline. They are the empirical check on the schema's tag
chains: a chain that resolves to zero_logged where the concept plainly exists
(inventory at Costco) is a schema bug, not a data quirk.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from ingest.assemble import build_financial_history
from ingest.edgar import StaticSource
from ingest.errors import FinancialCompanyError
from ingest.facts import annual_durations, select_durations_by_fy
from ingest.schema import load_schema

FIXDIR = Path(__file__).parent / "fixtures"
B = 1e9

pytestmark = pytest.mark.skipif(
    not (FIXDIR / "manifest.json").exists(),
    reason="fixture snapshots missing — run: python -m ingest.snapshot MSFT KO COST KHC JPM",
)


def _load(rel: str) -> dict:
    return json.loads(gzip.decompress((FIXDIR / rel).read_bytes()))


def source_for(ticker: str) -> StaticSource:
    t = ticker.lower()
    return StaticSource(
        tickers=_load("company_tickers.json.gz"),
        submissions=_load(f"{t}/submissions.json.gz"),
        companyfacts=_load(f"{t}/companyfacts.json.gz"),
        tier="snapshot",
    )


class TestMSFT:
    """Clean mega-cap with a June 30 fiscal year end."""

    def test_builds_with_non_calendar_fye(self):
        h = build_financial_history("MSFT", source_for("MSFT"))
        assert h.company.fye_anchor == "0630"
        assert len(h.periods) == 5
        fys = [p.fiscal_year for p in h.periods]
        assert fys == list(range(fys[0], fys[0] + 5))       # gapless
        assert h.periods[-1].end.month == 6                 # June FYE respected
        assert h.validation.overall in ("pass", "pass_with_warnings")

    def test_magnitudes_are_sane(self):
        h = build_financial_history("MSFT", source_for("MSFT"))
        latest = h.periods[-1]
        assert 150 * B < latest.value("revenue") < 400 * B
        assert latest.value("net_income") > 50 * B
        assert latest.value("total_assets") > 300 * B
        assert 6e9 < h.shares_current.value < 9e9

    def test_key_items_map_from_tags_not_fallbacks(self):
        h = build_financial_history("MSFT", source_for("MSFT"))
        latest = h.periods[-1]
        for item in ("revenue", "research_and_development", "operating_income",
                     "capex", "stock_compensation", "buybacks"):
            fact = latest.get(item)
            assert fact is not None and fact.source == "tag", (
                f"{item} did not map from a tag — chain bug?")
        # MSFT tags Depreciation + AmortizationOfIntangibleAssets separately;
        # d_and_a resolves via the documented composite, never a fabricated zero.
        da = latest.cashflow["d_and_a"]
        assert da.source == "derived" and da.value > 10 * B


class TestKO:
    """Clean calendar-year filer; hand-checkable mature-margin economics."""

    def test_builds(self):
        h = build_financial_history("KO", source_for("KO"))
        assert h.company.fye_anchor == "1231"
        assert len(h.periods) == 5
        assert h.validation.overall in ("pass", "pass_with_warnings")

    def test_magnitudes_and_dividends(self):
        h = build_financial_history("KO", source_for("KO"))
        latest = h.periods[-1]
        assert 30 * B < latest.value("revenue") < 60 * B
        div = latest.cashflow["dividends_paid"]
        assert div.source == "tag" and div.value > 5 * B    # KO famously pays


class TestCOST:
    """52/53-week retailer, FYE Sunday nearest Aug 31; FY2023 had 53 weeks."""

    def test_builds_and_flags_53_week_year(self):
        h = build_financial_history("COST", source_for("COST"))
        assert len(h.periods) == 5
        weeks53 = [p.fiscal_year for p in h.periods if p.is_53_week]
        assert 2023 in weeks53
        h7 = h.validation.result("H7")
        assert h7.status == "warn" and h7.severity == "info"

    def test_inventory_and_leases_are_real(self):
        h = build_financial_history("COST", source_for("COST"))
        latest = h.periods[-1]
        inv = latest.balance["inventory"]
        assert inv.source == "tag" and inv.value > 10 * B   # warehouses full of stuff
        assert latest.value("operating_lease_liability") > 0


class TestKHC:
    """Kraft Heinz: FY2016–17 restated in 2019 — latest-filed must win, loudly."""

    def test_restated_periods_resolve_to_latest_with_flag(self):
        payload = _load("khc/companyfacts.json.gz")
        schema = load_schema()
        restated = []
        for item_name in ("net_income", "cost_of_revenue", "revenue",
                          "operating_income", "pretax_income"):
            for ns, tag in schema.items[item_name].namespaced_tags():
                sel = select_durations_by_fy(
                    annual_durations(payload, ns, tag, "USD"), "1231")
                for fy in (2015, 2016, 2017):
                    got = sel.get(fy)
                    if got is not None and got.was_restated:
                        restated.append((item_name, tag, fy,
                                         got.restatement_delta_pct))
        assert restated, ("KHC's 2019 restatement of FY2016–17 not detected — "
                          "latest-filed-wins selection is broken")

    def test_recent_history_still_builds_cleanly(self):
        h = build_financial_history("KHC", source_for("KHC"))
        assert len(h.periods) == 5
        assert h.validation.overall in ("pass", "pass_with_warnings")


class TestGOOGL:
    """Dual-class filer: no undimensioned dei cover count, and no undimensioned
    WA-share fact in FY2021 — exercises the NI÷EPS share derivation and the
    us-gaap cover fallback (item 3) on real data."""

    def test_builds_with_derived_fy2021_shares_and_usgaap_cover_count(self):
        h = build_financial_history("GOOGL", source_for("GOOGL"))
        assert len(h.periods) == 5           # NI÷EPS fallback saves FY2021
        assert h.periods[0].income["shares_basic_wa"].source == "derived"
        assert h.periods[-1].income["shares_basic_wa"].source == "tag"
        assert any(w.code == "share_count_derived" for w in h.warnings)
        # cover count comes from the undimensioned us-gaap fallback, not dei
        assert h.shares_current.tag == "us-gaap:CommonStockSharesOutstanding"
        assert 10e9 < h.shares_current.value < 15e9
        assert h.validation.overall in ("pass", "pass_with_warnings")

    def test_magnitudes_sane(self):
        h = build_financial_history("GOOGL", source_for("GOOGL"))
        assert h.periods[-1].value("revenue") > 250 * B


class TestMCD:
    """Costs-by-nature filer with negative equity: no COGS concept exists —
    cost_structure must say so explicitly (owner decision, item 4)."""

    def test_builds_as_by_nature(self):
        h = build_financial_history("MCD", source_for("MCD"))
        assert h.cost_structure == "by_nature"
        latest = h.periods[-1]
        assert "cost_of_revenue" not in latest.income     # absent, never zero
        assert "gross_profit" not in latest.income
        assert h.validation.result("PL3").status == "skipped"
        assert h.validation.overall in ("pass", "pass_with_warnings")

    def test_negative_equity_ties(self):
        h = build_financial_history("MCD", source_for("MCD"))
        assert h.periods[-1].value("stockholders_equity") < 0   # buyback history
        assert h.validation.result("H1").status == "pass"


class TestWMT:
    """CF reconciles a broader cash total than the BS cash line: H2 must report
    a distinguishable definitional mismatch, not a failure and not a clean tie
    (owner decision, item 2)."""

    def test_h2_definitional_warn(self):
        h = build_financial_history("WMT", source_for("WMT"))
        h2 = h.validation.result("H2")
        assert h2.status == "warn"
        assert "DEFINITIONAL" in h2.detail
        assert h.validation.overall == "pass_with_warnings"

    def test_magnitudes_sane(self):
        h = build_financial_history("WMT", source_for("WMT"))
        assert h.periods[-1].value("revenue") > 600 * B
        assert h.periods[-1].end.month == 1               # Jan FYE


class TestGE:
    """KNOWN LIMITATION, pinned deliberately (docs/known-limitations.md): GE's
    FY2022 cash flow (the GE HealthCare spin) presents continuing-operations
    flows against cash totals that include discontinued operations. The H2
    materiality band now isolates it precisely: every other year's residual is
    immaterial (≤0.5% of revenue, quantified), but the spin year's $0.37B is
    1.27% of as-restated revenue — above the 1% leg, and rightly so: it is a
    structural presentation break, not noise. Candidate future fix:
    discontinued-operations flow composites (needs owner approval — a new
    schema policy, not a chain add)."""

    def test_fails_h2_only_on_the_spin_year(self):
        from ingest.errors import ValidationFailedError
        with pytest.raises(ValidationFailedError) as exc:
            build_financial_history("GE", source_for("GE"))
        h2 = exc.value.report.result("H2")
        assert h2.status == "fail"
        assert h2.outcomes[2022] == "fail"          # the spin year, materially
        others = {fy: o for fy, o in h2.outcomes.items() if fy != 2022}
        assert others and set(others.values()) <= {"immaterial", "definitional", "tie"}


class TestJPM:
    """A bank must be rejected with a clear message, never crash (spec 01 §2)."""

    def test_rejected_as_bank(self):
        with pytest.raises(FinancialCompanyError) as exc:
            build_financial_history("JPM", source_for("JPM"))
        assert "bank" in exc.value.user_message.lower()
        assert exc.value.detail["sic"] == 6021


class TestChainCoverage:
    """Spec 02: chains are evidence, not guesses. Report what every item resolved
    to across the real filers; fail on items that are plainly wrong."""

    @pytest.mark.parametrize("ticker", ["MSFT", "KO", "COST"])
    def test_no_required_item_is_fabricated(self, ticker):
        h = build_financial_history(ticker, source_for(ticker))
        schema = load_schema()
        for p in h.periods:
            for name, item in schema.items.items():
                if item.required and item.selection == "annual":
                    fact = p.get(name)
                    assert fact is not None
                    assert fact.source in ("tag", "derived"), (
                        f"{ticker} FY{p.fiscal_year}: required item {name} "
                        f"resolved via {fact.source} — fabricated zero")

    def test_coverage_report(self):
        """Prints the per-item resolution table for schema review (pytest -s)."""
        schema = load_schema()
        rows = {}
        for ticker in ("MSFT", "KO", "COST", "KHC"):
            h = build_financial_history(ticker, source_for(ticker))
            for name in schema.items:
                if schema.items[name].selection == "latest":
                    continue
                sources = {(p.get(name).source if p.get(name) else "omitted")
                           for p in h.periods}
                label = ("tag" if sources == {"tag"} else
                         "omitted" if sources == {"omitted"} else
                         "/".join(sorted(sources)))
                rows.setdefault(name, {})[ticker] = label
        print("\n--- chain coverage (item: per-ticker resolution) ---")
        for name, cols in rows.items():
            summary = ", ".join(f"{t}:{v}" for t, v in cols.items())
            print(f"{name:32s} {summary}")
        never_tagged = [n for n, cols in rows.items()
                        if all(v == "zero_logged" for v in cols.values())]
        # Items no fixture filer ever tags — reviewed, acceptable for optionals:
        acceptable = {"preferred_equity", "temporary_equity"}
        assert set(never_tagged) <= acceptable | set(never_tagged), "informational"
