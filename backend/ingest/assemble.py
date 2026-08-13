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
    KnownUnsupportedError,
    MissingRequiredItemError,
    ValidationFailedError,
)
from .facts import iter_raw_facts, select_latest
from .mapping import map_history
from .models import CompanyMeta, Fact, FinancialHistory, IngestWarning
from .schema import load_schema
from .validation import validate_history

KNOWN_UNSUPPORTED_PATH = Path(__file__).parent / "known_unsupported.yaml"


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
