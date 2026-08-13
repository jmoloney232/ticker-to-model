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


class EdgarUnavailableError(IngestError):
    def __init__(self, what: str):
        super().__init__(
            f"SEC EDGAR is unreachable and no cached or snapshot data exists for {what}. "
            "Please try again later.",
            {"what": what},
        )
