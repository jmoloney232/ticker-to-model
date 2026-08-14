"""Typed error taxonomy for ingest (specs/01-ingest.md, Error cases).

Every user-facing failure says what failed, why, and what still works.
"""

from __future__ import annotations


class IngestError(Exception):
    """Base for all ingest failures. `user_message` is safe to show verbatim."""

    def __init__(self, user_message: str, detail: dict | None = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail or {}


class UnknownTickerError(IngestError):
    def __init__(self, ticker: str):
        super().__init__(f"Ticker {ticker!r} not found in SEC EDGAR's company list.",
                         {"ticker": ticker})


class FinancialCompanyError(IngestError):
    def __init__(self, ticker: str, name: str, sic: int, category: str):
        super().__init__(
            f"{name} ({ticker}) is a {category} (SIC {sic}). Bank, insurer, and REIT "
            "financial statements have a fundamentally different structure and are not "
            "supported.",
            {"ticker": ticker, "sic": sic, "category": category},
        )


class UnsupportedCurrencyError(IngestError):
    def __init__(self, ticker: str, units_seen: list[str]):
        super().__init__(
            f"{ticker} does not report in USD (units seen: {', '.join(units_seen) or 'none'}). "
            "Only USD reporters are supported.",
            {"ticker": ticker, "units_seen": units_seen},
        )


class InsufficientHistoryError(IngestError):
    def __init__(self, ticker: str, found: int, needed: int, reason: str = ""):
        msg = (f"{ticker} has {found} usable fiscal year(s); {needed} are required."
               + (f" {reason}" if reason else ""))
        super().__init__(msg, {"ticker": ticker, "found": found, "needed": needed,
                               "reason": reason})


class MissingRequiredItemError(IngestError):
    def __init__(self, ticker: str, item: str, fiscal_year: int, tags_tried: list[str]):
        super().__init__(
            f"Required line item {item!r} could not be mapped for {ticker} FY{fiscal_year}. "
            f"Tags tried: {', '.join(tags_tried)}. Refusing to build a model with a "
            "fabricated zero.",
            {"ticker": ticker, "item": item, "fiscal_year": fiscal_year,
             "tags_tried": tags_tried},
        )


class ValidationFailedError(IngestError):
    """Historical tie-out failure (spec 07). Carries the full report."""

    def __init__(self, ticker: str, report):
        failing = [r.check_id for r in report.results
                   if r.severity == "fail" and r.status == "fail"]
        super().__init__(
            f"{ticker}: assembled statements do not tie ({', '.join(failing)}). "
            "The model will not be built from numbers that don't reconcile.",
            {"ticker": ticker, "failing": failing},
        )
        self.report = report


class InsufficientCoverageError(IngestError):
    """Coverage gate (owner-approved 2026-08-13): too little of the balance
    sheet maps to named line items to value honestly. The message is
    diagnostic — it names the largest unattributed balances — not generic."""

    def __init__(self, ticker: str, assets_share: float, liabilities_share: float,
                 floor: float, largest: list[str]):
        largest_str = "; ".join(largest) or "n/a"
        super().__init__(
            f"{ticker} maps only {assets_share:.0%} of assets and "
            f"{liabilities_share:.0%} of liabilities to named line items "
            f"(floor: {floor:.0%}). A valuation built from that little of the "
            f"balance sheet would be confidently wrong, so none is produced. "
            f"Largest unattributed balances: {largest_str}.",
            {"ticker": ticker, "assets_named_share": assets_share,
             "liabilities_named_share": liabilities_share, "floor": floor,
             "largest_unattributed": largest},
        )


class KnownUnsupportedError(IngestError):
    """Filer on the known-unsupported list (ingest/known_unsupported.yaml)."""

    def __init__(self, ticker: str, reason: str):
        super().__init__(
            f"{ticker} uses filing conventions we don't support yet: {reason}",
            {"ticker": ticker, "reason": reason},
        )


class EdgarUnavailableError(IngestError):
    def __init__(self, what: str):
        super().__init__(
            f"SEC EDGAR is unreachable and no cached or snapshot data exists for {what}. "
            "Please try again later.",
            {"what": what},
        )
