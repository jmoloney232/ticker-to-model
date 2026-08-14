"""MarketDataProvider protocol + the degradation ladder (specs/03-market-data.md).

Mirror of ingest's EdgarSource/EdgarClient split: the protocol is what callers
see; `LadderedProvider` runs live → fresh cache → stale cache → snapshot per
source independently, labeling every value with its tier; `StaticMarketProvider`
is the test seam. No vendor type leaks past this module's boundary.
"""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from ingest.models import Tier

from .alpaca import AlpacaClient
from .errors import MarketDataError, MarketDataUnavailableError
from .fred import FredClient
from .models import Bar, PricePoint, RatePoint

SNAPSHOT_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "market"
FRESH_S = 24 * 3600.0
# Beta needs 2y of weekly samples; the extra days stabilize the first ISO week.
BARS_WINDOW_DAYS = 741


def market_today() -> date:
    """Today on the US market calendar (a UTC date would roll over at 8pm ET)."""
    return datetime.now(tz=ZoneInfo("America/New_York")).date()


class MarketDataProvider(Protocol):
    def get_bars(self, symbol: str, start: date, end: date) -> tuple[list[Bar], Tier]: ...
    def get_latest_price(self, symbol: str) -> PricePoint: ...
    def get_risk_free(self) -> RatePoint: ...


def _decode_bars(payload: dict) -> list[Bar]:
    return [Bar(day=date.fromisoformat(d), close=float(c)) for d, c in payload["bars"]]


class LadderedProvider:
    """Live vendors behind the cache/snapshot ladder.

    Bars are cached per symbol with a 24h freshness window; a fresh hit serves
    regardless of the exact requested range because every caller uses the same
    rolling-2y shape — one refresh a day is the intended behavior, and a bar
    window one day older than requested is immaterial to a 104-week regression
    (the latest price then carries the `cache` label, one trading day stale).
    """

    def __init__(self, alpaca: AlpacaClient, fred: FredClient, cache,
                 snapshot_dir: Path = SNAPSHOT_DIR):
        self.alpaca = alpaca
        self.fred = fred
        self.cache = cache
        self.snapshot_dir = snapshot_dir

    # ── bars + price ───────────────────────────────────────────────────────
    def get_bars(self, symbol: str, start: date, end: date) -> tuple[list[Bar], Tier]:
        key = f"bars:{symbol}"
        cached = self.cache.get(key, FRESH_S)
        if cached is not None and cached[1]:
            return _decode_bars(cached[0]), "cache"
        try:
            bars = self.alpaca.daily_bars(symbol, start, end)
            if bars:
                self.cache.put(key, {
                    "symbol": symbol, "start": start.isoformat(),
                    "end": end.isoformat(),
                    "bars": [[b.day.isoformat(), b.close] for b in bars]})
                return bars, "live"
        except MarketDataError:
            pass
        if cached is not None:                       # stale but real
            return _decode_bars(cached[0]), "stale_cache"
        snap = self._load_snapshot(f"{symbol.lower()}_bars.json.gz")
        if snap is not None:
            return _decode_bars(snap), "snapshot"
        raise MarketDataUnavailableError(f"{symbol} bars")

    def get_latest_price(self, symbol: str) -> PricePoint:
        today = market_today()
        bars, tier = self.get_bars(symbol, today - timedelta(days=BARS_WINDOW_DAYS), today)
        last = bars[-1]
        return PricePoint(value=last.close, as_of=last.day, staleness=tier)

    # ── risk-free ──────────────────────────────────────────────────────────
    def get_risk_free(self) -> RatePoint:
        key = "rate:dgs10"
        cached = self.cache.get(key, FRESH_S)
        if cached is not None and cached[1]:
            return RatePoint(cached[0]["value"], date.fromisoformat(cached[0]["date"]),
                             "cache")
        try:
            value, as_of = self.fred.dgs10()
            self.cache.put(key, {"value": value, "date": as_of.isoformat()})
            return RatePoint(value, as_of, "live")
        except MarketDataError:
            pass
        if cached is not None:
            return RatePoint(cached[0]["value"], date.fromisoformat(cached[0]["date"]),
                             "stale_cache")
        snap = self._load_snapshot("dgs10.json.gz")
        if snap is not None:
            return RatePoint(snap["value"], date.fromisoformat(snap["date"]), "snapshot")
        raise MarketDataUnavailableError("10Y Treasury yield (DGS10)")

    def _load_snapshot(self, filename: str) -> dict | None:
        path = self.snapshot_dir / filename
        if not path.exists():
            return None
        return json.loads(gzip.decompress(path.read_bytes()))


class StaticMarketProvider:
    """Deterministic provider for tests and committed-snapshot runs."""

    def __init__(self, bars: dict[str, list[Bar]], risk_free: float = 0.042,
                 rf_date: date | None = None, tier: Tier = "snapshot"):
        self.bars = bars
        self.risk_free = risk_free
        self.rf_date = rf_date or date(2026, 8, 12)
        self.tier: Tier = tier

    def get_bars(self, symbol: str, start: date, end: date) -> tuple[list[Bar], Tier]:
        if symbol not in self.bars:
            raise MarketDataUnavailableError(f"{symbol} bars")
        return [b for b in self.bars[symbol] if start <= b.day <= end], self.tier

    def get_latest_price(self, symbol: str) -> PricePoint:
        if symbol not in self.bars or not self.bars[symbol]:
            raise MarketDataUnavailableError(f"{symbol} price")
        last = self.bars[symbol][-1]
        return PricePoint(value=last.close, as_of=last.day, staleness=self.tier)

    def get_risk_free(self) -> RatePoint:
        return RatePoint(self.risk_free, self.rf_date, self.tier)
