"""Orchestrator: ticker -> validated FinancialHistory (spec 01, Pipeline).

The one public entry point of the ingest package. Pure given its EdgarSource —
tests inject StaticSource; the app injects EdgarClient.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from .edgar import EdgarSource
from .errors import (
    FinancialCompanyError,
    InsufficientCoverageError,
    KnownUnsupportedError,
    MissingRequiredItemError,
    ValidationFailedError,
)
from .facts import iter_raw_facts, select_latest
from .mapping import MappedHistory, map_history
from .models import CompanyMeta, Fact, FinancialHistory, IngestWarning
from .schema import load_schema
from .validation import validate_history

KNOWN_UNSUPPORTED_PATH = Path(__file__).parent / "known_unsupported.yaml"

# Coverage gate (owner-approved 2026-08-13), on min(assets, liabilities)
# named-share. Calibrated on the 29-ticker scan, where the distribution is
# bimodal: DE at 20%/18% (a captive-finance balance sheet our chains don't
# know), then nothing until NVDA at 73%, then ≥86%. The 60% floor separates
# most-of-the-balance-sheet disasters from real but bounded gaps; the 60–85%
# band builds behind a hard, non-dismissible warning (UI contract, spec 06) —
# a filer that builds badly is more dangerous than one that fails, because
# nothing tells the user to distrust it.
COVERAGE_REFUSE_FLOOR = 0.60
COVERAGE_WARN_FLOOR = 0.85


@lru_cache(maxsize=1)
def known_unsupported() -> dict[str, str]:
    rows = yaml.safe_load(KNOWN_UNSUPPORTED_PATH.read_text()) or []
    return {r["ticker"].upper(): r["reason"].strip() for r in rows}

# SIC-based rejection of financial companies (spec 01 §2).
_SIC_CATEGORIES: list[tuple[range, str]] = [
    (range(6020, 6200), "bank or credit institution"),
    (range(6300, 6500), "insurance company"),
    (range(6722, 6723), "investment fund"),
    (range(6726, 6727), "investment fund"),
    (range(6798, 6799), "REIT"),
]


def _reject_financial(ticker: str, name: str, sic: int | None) -> None:
    if sic is None:
        return
    for sic_range, category in _SIC_CATEGORIES:
        if sic in sic_range:
            raise FinancialCompanyError(ticker, name, sic, category)


def _largest_unattributed(mapped: MappedHistory, limit: int = 6) -> list[str]:
    """The residual buckets we had to derive, largest first, then the biggest
    unmapped balance-sheet tags as candidates for where the value actually is."""
    p = mapped.periods[-1]
    buckets = [(abs(f.value), f"{name} ${f.value / 1e9:.1f}B (derived residual)")
               for name in ("other_current_assets", "other_noncurrent_assets",
                            "other_current_liabilities", "other_noncurrent_liabilities")
               if (f := p.balance.get(name)) is not None and f.source == "derived"
               and abs(f.value) > 0.0]
    out = [label for _, label in sorted(buckets, reverse=True)]
    out += [f"unmapped tag {u.tag} ${u.value / 1e9:.1f}B"
            for u in mapped.coverage.top_unmapped if u.shape == "instant"][:3]
    return out[:limit]


def _coverage_gate(ticker: str, mapped: MappedHistory) -> None:
    cov = mapped.coverage
    gate = min(cov.assets_named_share, cov.liabilities_named_share)
    if gate >= COVERAGE_WARN_FLOOR:
        return
    largest = _largest_unattributed(mapped)
    if gate < COVERAGE_REFUSE_FLOOR:
        raise InsufficientCoverageError(ticker, cov.assets_named_share,
                                        cov.liabilities_named_share,
                                        COVERAGE_REFUSE_FLOOR, largest)
    mapped.warnings.append(IngestWarning(
        code="coverage_low",
        message=(f"Only {cov.assets_named_share:.0%} of assets / "
                 f"{cov.liabilities_named_share:.0%} of liabilities map to named "
                 f"line items (clean-build floor: {COVERAGE_WARN_FLOOR:.0%}). "
                 f"Defaults derived from residual buckets deserve scrutiny. "
                 f"Largest unattributed: {'; '.join(largest)}."),
        detail={"assets_named_share": cov.assets_named_share,
                "liabilities_named_share": cov.liabilities_named_share,
                "warn_floor": COVERAGE_WARN_FLOOR}))


def _immaterial_residual_warning(report, mapped: MappedHistory) -> IngestWarning | None:
    """Surface H2's immaterial unreconciled residuals as a structured warning
    in the assembled output (owner requirement: dollars, percentage, and
    affected years visible to the UI — not buried in a log)."""
    h2 = report.result("H2")
    if h2 is None:
        return None
    years = sorted(fy for fy, o in h2.outcomes.items() if o == "immaterial")
    if not years:
        return None
    lines = []
    for p in mapped.periods:
        if p.fiscal_year not in years:
            continue
        resid = h2.per_period[p.fiscal_year]
        gross = (abs(p.value("cash_from_operations"))
                 + abs(p.value("cash_from_investing"))
                 + abs(p.value("cash_from_financing")))
        lines.append(f"FY{p.fiscal_year}: ${resid / 1e9:.2f}B = "
                     f"{resid / p.value('revenue'):.2%} of revenue, "
                     f"{resid / gross:.2%} of gross cash flows")
    return IngestWarning(
        code="immaterial_cash_residual",
        message=("Cash flow statement leaves an unreconciled residual below the "
                 "materiality threshold — quantified and disclosed, not fatal: "
                 + "; ".join(lines)),
        detail={"years": years,
                "residuals": {fy: h2.per_period[fy] for fy in years}})


def build_financial_history(ticker: str, source: EdgarSource,
                            years: int = 5) -> FinancialHistory:
    ticker = ticker.strip().upper()
    if ticker in known_unsupported():
        raise KnownUnsupportedError(ticker, known_unsupported()[ticker])
    cik = source.resolve_cik(ticker)

    submissions, sub_tier = source.get_submissions(cik)
    name = submissions.get("name") or ticker
    sic_raw = submissions.get("sic")
    sic = int(sic_raw) if sic_raw not in (None, "") else None
    _reject_financial(ticker, name, sic)

    company = CompanyMeta(
        cik=cik,
        ticker=ticker,
        name=name,
        sic=sic,
        sic_description=submissions.get("sicDescription", ""),
        fye_anchor=submissions.get("fiscalYearEnd") or "1231",
    )

    payload, facts_tier = source.get_companyfacts(cik)
    schema = load_schema()
    mapped = map_history(payload, schema, company.fye_anchor, ticker, years)
    _coverage_gate(ticker, mapped)

    shares_item = schema.items["shares_outstanding"]
    shares_current = None
    for ns, tag in shares_item.namespaced_tags():
        latest = select_latest(iter_raw_facts(payload, ns, tag, shares_item.unit))
        if latest is not None:
            shares_current = Fact(
                value=latest.value, unit=latest.unit, tag=f"{ns}:{tag}", source="tag",
                accession=latest.accession, filed=latest.filed, end=latest.end,
                first_filed_value=latest.first_filed_value,
                was_restated=latest.was_restated,
                restatement_delta_pct=latest.restatement_delta_pct,
            )
            break
    if shares_current is None:
        # Last resort (META — verified): no undimensioned share count anywhere;
        # fall back to the latest fiscal year's basic WA count, loudly. The
        # share count feeds per-share value — provenance must be visible.
        wa = mapped.periods[-1].income.get("shares_basic_wa")
        if wa is None:
            raise MissingRequiredItemError(ticker, shares_item.name,
                                           mapped.periods[-1].fiscal_year,
                                           list(shares_item.tags))
        shares_current = Fact(value=wa.value, unit="shares", tag="derived",
                              source="derived", end=mapped.periods[-1].end)
        mapped.warnings.append(IngestWarning(
            code="share_count_derived",
            message=("Current share count unavailable under any undimensioned tag; "
                     f"using FY{mapped.periods[-1].fiscal_year} basic weighted-average "
                     "as the current-count proxy."),
            item="shares_outstanding"))

    report = validate_history(mapped)
    if report.overall == "fail":
        raise ValidationFailedError(ticker, report)
    imm = _immaterial_residual_warning(report, mapped)
    if imm is not None:
        mapped.warnings.append(imm)

    return FinancialHistory(
        company=company,
        periods=mapped.periods,
        shares_current=shares_current,
        warnings=mapped.warnings,
        validation=report,
        staleness={"submissions": sub_tier, "companyfacts": facts_tier},
        coverage=mapped.coverage,
        cost_structure=mapped.cost_structure,
    )
