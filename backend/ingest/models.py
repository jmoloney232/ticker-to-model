"""Output data shapes for ingest (specs/01-ingest.md, Outputs).

Plain dataclasses on purpose: the engine consumes these and must stay free of
framework imports (CLAUDE.md, engine purity).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Severity = Literal["fail", "warn", "info"]
CheckStatus = Literal["pass", "fail", "warn", "skipped"]
SourceKind = Literal["tag", "derived", "zero_logged"]
Tier = Literal["live", "cache", "stale_cache", "snapshot"]


@dataclass(frozen=True)
class Fact:
    """One selected value with full provenance (invariant: every number has it)."""

    value: float
    unit: str
    tag: str                      # winning tag ("us-gaap:X" / "dei:X") or "derived"
    source: SourceKind
    accession: str | None = None
    filed: date | None = None
    end: date | None = None
    first_filed_value: float | None = None
    was_restated: bool = False
    restatement_delta_pct: float | None = None


@dataclass
class FiscalPeriod:
    fiscal_year: int              # labeled by calendar year nearest the FYE
    start: date
    end: date
    duration_days: int
    is_53_week: bool
    income: dict[str, Fact] = field(default_factory=dict)
    balance: dict[str, Fact] = field(default_factory=dict)
    cashflow: dict[str, Fact] = field(default_factory=dict)

    def get(self, item: str) -> Fact | None:
        return self.income.get(item) or self.balance.get(item) or self.cashflow.get(item)

    def value(self, item: str, default: float | None = None) -> float | None:
        f = self.get(item)
        return f.value if f is not None else default


@dataclass
class CompanyMeta:
    cik: int
    ticker: str
    name: str
    sic: int | None
    sic_description: str
    fye_anchor: str               # MMDD from submissions, e.g. "0630"
    currency: str = "USD"


@dataclass
class IngestWarning:
    code: str                     # unmapped_item | restated | week53 | ...
    message: str
    fiscal_year: int | None = None
    item: str | None = None
    detail: dict = field(default_factory=dict)


@dataclass
class CheckResult:
    check_id: str                 # H1..H7
    severity: Severity
    status: CheckStatus
    magnitude: float | None = None    # worst offending residual across periods
    tolerance: float | None = None
    detail: str = ""
    per_period: dict[int, float] = field(default_factory=dict)
    outcomes: dict[int, str] = field(default_factory=dict)
    # Per-fiscal-year disposition labels for checks with more than pass/fail
    # semantics (H2: tie | definitional | immaterial | fail — owner decision:
    # the three non-fail dispositions must be separately identifiable by the
    # UI, never collapsed into one warning type). Empty for binary checks.


@dataclass
class ValidationReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def overall(self) -> str:
        if any(r.severity == "fail" and r.status == "fail" for r in self.results):
            return "fail"
        if any(r.status == "warn" and r.severity != "info" for r in self.results):
            return "pass_with_warnings"
        return "pass"

    def result(self, check_id: str) -> CheckResult | None:
        return next((r for r in self.results if r.check_id == check_id), None)


@dataclass
class UnmappedTag:
    """A filed us-gaap tag no chain consumes, ranked by magnitude for review."""

    tag: str
    value: float
    shape: str                    # duration | instant


@dataclass
class Coverage:
    """How much of the filing landed in named line items vs. residual buckets.

    Shares are 1 − |residual other_* buckets we derived| / total: a filer's own
    tagged "other" line counts as mapped (that is their bucket); a residual WE
    had to derive is the unmapped remainder. A falling share is the signal that
    a filer uses tags the schema doesn't know about (spec 01).
    Computed on the latest fiscal year.
    """

    fiscal_year: int
    assets_named_share: float
    liabilities_named_share: float
    expenses_named_share: float | None    # None when revenue − EBIT is non-positive
    revenue_named_share: float
    top_unmapped: list[UnmappedTag] = field(default_factory=list)


@dataclass
class FinancialHistory:
    company: CompanyMeta
    periods: list[FiscalPeriod]           # ascending fiscal_year, gapless
    shares_current: Fact                  # latest cover-page count, any form
    warnings: list[IngestWarning]
    validation: ValidationReport
    staleness: dict[str, Tier]            # per EDGAR endpoint
    coverage: Coverage | None = None
    cost_structure: str = "by_function"   # by_function | by_nature — downstream
                                          # code (engine margins, Excel IS block,
                                          # UI) branches on this explicit field

    @property
    def latest(self) -> FiscalPeriod:
        return self.periods[-1]
