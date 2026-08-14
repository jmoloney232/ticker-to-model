"""Typed error taxonomy for market data (specs/03-market-data.md, Error cases).

Invariant: API keys never appear in messages — vendor adapters strip query
strings and never echo headers.
"""

from __future__ import annotations


class MarketDataError(Exception):
    """Base for market-data failures. `user_message` is safe to show verbatim."""

    def __init__(self, user_message: str, detail: dict | None = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail or {}


class InsufficientPriceHistoryError(MarketDataError):
    def __init__(self, symbol: str, n_obs: int, minimum: int):
        super().__init__(
            f"{symbol} has {n_obs} paired weekly observations; {minimum} are required "
            "for a beta regression (recent IPO?). The engine will fall back to "
            "beta = 1.0 with a warning.",
            {"symbol": symbol, "n_obs": n_obs, "minimum": minimum},
        )


class MarketDataUnavailableError(MarketDataError):
    def __init__(self, what: str):
        super().__init__(
            f"Market data for {what} is unavailable: the live source failed and no "
            "cached or snapshot data exists. Historicals and assumptions still work; "
            "the DCF needs this input.",
            {"what": what},
        )


class BenchmarkUnavailableError(MarketDataError):
    def __init__(self, benchmark: str = "SPY"):
        super().__init__(
            f"Benchmark series {benchmark} is unavailable at every tier — beta cannot "
            "be computed. The engine will fall back to beta = 1.0 with a warning.",
            {"benchmark": benchmark},
        )
