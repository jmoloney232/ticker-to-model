"""Per-(ticker, valuation_date) company cache (specs/06-webapp.md).

Owner requirement: assumption edits must never re-trigger EDGAR or market
fetches — the assembled history and market inputs are fetched once per
(ticker, valuation date) and reused across recomputes. The reverse-DCF solve
(market-implied assumptions) is independent of user edits by construction
(it solves against derived defaults), so it is computed once here too.

Refusals (coverage gate, unsupported filers, ...) are cached like successes —
they are deterministic verdicts on the same data. Transient failures
(EDGAR unreachable with nothing cached) are NOT cached, so recovery is
immediate when the upstream returns.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date

from engine.reverse import ImpliedResult, implied_all
from ingest.assemble import build_financial_history
from ingest.errors import (
    FinancialCompanyError,
    IngestError,
    InsufficientCoverageError,
    InsufficientHistoryError,
    KnownUnsupportedError,
    MissingRequiredItemError,
    UnsupportedCurrencyError,
    ValidationFailedError,
)
from market.assemble import build_market_inputs

# Deterministic verdicts on the filer's data: safe to cache, returned as 200s.
REFUSAL_ERRORS = (
    FinancialCompanyError,
    KnownUnsupportedError,
    UnsupportedCurrencyError,
    InsufficientHistoryError,
    MissingRequiredItemError,
    ValidationFailedError,
    InsufficientCoverageError,
)


@dataclass
class CompanyData:
    history: object | None = None
    market: object | None = None
    refusal: IngestError | None = None
    reverse: dict[str, ImpliedResult] | None = None
    created: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)


class CompanyStore:
    def __init__(self, edgar_for, provider_for, ttl: float = 3600.0,
                 cap: int = 64):
        self._edgar_for = edgar_for
        self._provider_for = provider_for
        self._ttl = ttl
        self._cap = cap
        self._entries: OrderedDict[tuple[str, date], CompanyData] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, ticker: str, vd: date) -> CompanyData:
        """History + market for (ticker, vd), fetched once. Raises transient
        errors (unknown ticker, upstream down); refusals come back as data."""
        key = (ticker.upper(), vd)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and time.monotonic() - entry.created < self._ttl:
                self._entries.move_to_end(key)
                return entry
            entry = CompanyData()
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self._cap:
                self._entries.popitem(last=False)
        with entry.lock:
            if entry.history is None and entry.refusal is None:
                try:
                    entry.history = build_financial_history(
                        key[0], self._edgar_for(key[0]))
                    entry.market = build_market_inputs(
                        key[0], self._provider_for(key[0]), as_of=vd)
                except REFUSAL_ERRORS as exc:
                    entry.refusal = exc
                except Exception:
                    with self._lock:          # transient: never cached
                        self._entries.pop(key, None)
                    raise
        return entry

    def reverse(self, ticker: str, vd: date) -> dict[str, ImpliedResult]:
        """Market-implied solves — solved against derived defaults, so user
        edits never invalidate this; computed once per (ticker, vd)."""
        entry = self.get(ticker, vd)
        if entry.refusal is not None:
            raise entry.refusal
        with entry.lock:
            if entry.reverse is None:
                entry.reverse = implied_all(entry.history, entry.market, vd)
        return entry.reverse
