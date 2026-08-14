"""Output data shapes for market data (specs/03-market-data.md, Outputs).

Plain dataclasses, same discipline as ingest: every value carries its
degradation tier so staleness is labelable everywhere downstream. The engine
consumes MarketInputs only — no vendor type crosses this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ingest.models import Tier  # shared staleness vocabulary across the app


@dataclass(frozen=True)
class Bar:
    """One daily bar. Close only — beta and price need nothing else, and a
    smaller shape keeps snapshots reviewable. ALWAYS split-adjusted."""

    day: date
    close: float


@dataclass(frozen=True)
class PricePoint:
    value: float
    as_of: date
    staleness: Tier


@dataclass(frozen=True)
class RatePoint:
    value: float                  # decimal (0.042, never 4.2)
    as_of: date
    staleness: Tier


@dataclass(frozen=True)
class BetaResult:
    raw: float
    adjusted: float               # Blume: 2/3·raw + 1/3
    n_obs: int                    # paired weekly return observations
    r_squared: float
    window_start: date
    window_end: date
    frequency: str = "weekly"
    benchmark: str = "SPY"
    staleness: Tier = "live"


@dataclass
class MarketWarning:
    """Mirrors ingest's warning shape so the engine can merge both streams."""

    code: str
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class MarketInputs:
    """Everything the engine needs from markets, staleness-labeled per field.

    `beta` is None when it could not be computed (short price history, or the
    benchmark unavailable at every tier) — the engine substitutes 1.0 with a
    loud warning; price and risk_free are hard requirements and their absence
    raises before this object exists.
    """

    ticker: str
    price: PricePoint
    risk_free: RatePoint
    beta: BetaResult | None
    warnings: list[MarketWarning] = field(default_factory=list)
