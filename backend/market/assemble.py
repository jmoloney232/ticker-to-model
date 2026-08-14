"""Orchestrator: ticker -> MarketInputs (mirror of ingest.assemble).

Price and the risk-free rate are hard requirements — their ladder exhaustion
raises. Beta is the one market input with a documented fallback (β = 1.0), so
short history or a missing benchmark degrade to a loud warning instead of
killing the DCF.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from ingest.models import Tier

from .beta import compute_beta
from .errors import InsufficientPriceHistoryError, MarketDataUnavailableError
from .models import MarketInputs, MarketWarning
from .provider import BARS_WINDOW_DAYS, MarketDataProvider, market_today

BENCHMARK = "SPY"

_TIER_RANK: dict[Tier, int] = {"live": 0, "cache": 1, "stale_cache": 2, "snapshot": 3}


def _worst(*tiers: Tier) -> Tier:
    return max(tiers, key=lambda t: _TIER_RANK[t])


def build_market_inputs(ticker: str, provider: MarketDataProvider,
                        as_of: date | None = None) -> MarketInputs:
    ticker = ticker.strip().upper()
    as_of = as_of or market_today()
    warnings: list[MarketWarning] = []

    price = provider.get_latest_price(ticker)          # raises when exhausted
    risk_free = provider.get_risk_free()               # raises when exhausted

    start = as_of - timedelta(days=BARS_WINDOW_DAYS)
    beta = None
    try:
        stock_bars, stock_tier = provider.get_bars(ticker, start, as_of)
        bench_bars, bench_tier = provider.get_bars(BENCHMARK, start, as_of)
        beta = replace(compute_beta(ticker, stock_bars, bench_bars, start, as_of),
                       staleness=_worst(stock_tier, bench_tier))
    except InsufficientPriceHistoryError as exc:
        warnings.append(MarketWarning(
            code="beta_fallback",
            message=(f"Only {exc.detail['n_obs']} paired weekly observations "
                     f"(minimum {exc.detail['minimum']}) — market-average beta 1.0 "
                     "assumed; override recommended."),
            detail=exc.detail))
    except MarketDataUnavailableError as exc:
        warnings.append(MarketWarning(
            code="benchmark_unavailable" if BENCHMARK in exc.user_message
            else "beta_unavailable",
            message=(f"Beta could not be computed ({exc.user_message.split(':')[0]}) — "
                     "market-average beta 1.0 assumed; override recommended."),
            detail=exc.detail))

    return MarketInputs(ticker=ticker, price=price, risk_free=risk_free,
                        beta=beta, warnings=warnings)
