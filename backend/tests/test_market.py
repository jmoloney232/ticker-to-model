"""Market data module tests (specs/03-market-data.md, How tested).

Everything offline: synthetic bar series with known properties, httpx
MockTransport for the vendor adapters, and a fake-vendor ladder harness.
The live smoke test at the bottom is skipped without credentials.
"""

from __future__ import annotations

import gzip
import json
import os
import random
from datetime import date, timedelta

import httpx
import pytest

from ingest.cache import NullCache, SqliteCache
from market.alpaca import AlpacaClient
from market.assemble import build_market_inputs
from market.beta import compute_beta
from market.errors import (
    InsufficientPriceHistoryError,
    MarketDataError,
    MarketDataUnavailableError,
)
from market.fred import FredClient
from market.models import Bar
from market.provider import SNAPSHOT_DIR, LadderedProvider, StaticMarketProvider

START = date(2024, 8, 12)          # a Monday
END = date(2026, 8, 12)


def weekdays(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def series_from_returns(days: list[date], returns: list[float],
                        initial: float = 100.0) -> list[Bar]:
    bars, price = [], initial
    for day, r in zip(days, [0.0, *returns], strict=True):
        price *= 1.0 + r
        bars.append(Bar(day=day, close=price))
    return bars


def correlated_pair(slope: float = 1.3, noise: float = 0.002,
                    seed: int = 42) -> tuple[list[Bar], list[Bar]]:
    """Stock daily returns = slope × market + ε with tiny noise, so the weekly
    regression must recover ~slope. Expected values are properties of the
    construction, not of the code under test."""
    rng = random.Random(seed)
    days = weekdays(START, END)
    mkt = [rng.gauss(0.0, 0.01) for _ in range(len(days) - 1)]
    stk = [slope * r + rng.gauss(0.0, noise) for r in mkt]
    return series_from_returns(days, stk), series_from_returns(days, mkt)


class TestBetaComputation:
    def test_recovers_known_slope_and_blume_is_exact(self):
        stock, mkt = correlated_pair(slope=1.3)
        b = compute_beta("SYN", stock, mkt, START, END)
        assert b.raw == pytest.approx(1.3, abs=0.05)
        assert b.adjusted == pytest.approx(2 / 3 * b.raw + 1 / 3)   # Blume, exact
        assert 95 <= b.n_obs <= 105                                 # ~104 weeks target
        assert b.r_squared > 0.9
        assert b.frequency == "weekly" and b.benchmark == "SPY"

    def test_min_observations_enforced(self):
        stock, mkt = correlated_pair()
        short_start = END - timedelta(days=400)      # ~57 weeks < 80 minimum
        with pytest.raises(InsufficientPriceHistoryError) as exc:
            compute_beta("IPO", stock, mkt, short_start, END)
        assert exc.value.detail["minimum"] == 80
        assert exc.value.detail["n_obs"] < 80

    def test_missing_week_drops_the_pair_no_forward_fill(self):
        stock, mkt = correlated_pair()
        full = compute_beta("SYN", stock, mkt, START, END)
        gap_week = date(2025, 3, 10)                 # remove one full ISO week
        gapped = [b for b in stock
                  if not gap_week <= b.day < gap_week + timedelta(days=7)]
        gapped_result = compute_beta("SYN", gapped, mkt, START, END)
        # the missing week kills its into- and out-of- returns; the 14-day span
        # across the hole is NOT smeared into a fake "weekly" return
        assert gapped_result.n_obs == full.n_obs - 2

    def test_holiday_mismatch_samples_last_common_day(self):
        stock, mkt = correlated_pair()
        week = date(2025, 6, 2)                      # Mon of an ordinary week
        stock2 = [b for b in stock if b.day not in (week, week + timedelta(days=1))]
        mkt2 = [b for b in mkt
                if b.day not in (week + timedelta(days=3), week + timedelta(days=4))]
        full = compute_beta("SYN", stock, mkt, START, END)
        b = compute_beta("SYN", stock2, mkt2, START, END)
        # both series sample that week on Wednesday (last COMMON trading day):
        # the week survives, n_obs unchanged, slope essentially unmoved
        assert b.n_obs == full.n_obs
        assert b.raw == pytest.approx(full.raw, abs=0.03)

    def test_adjusted_split_series_is_clean(self):
        _, mkt = correlated_pair(slope=1.0, noise=0.0)
        # stock == market exactly, with closes retroactively split-adjusted
        # (continuous series): beta must be exactly 1
        stock = [Bar(day=b.day, close=b.close / 10.0) for b in mkt]
        b = compute_beta("SPLIT", stock, mkt, START, END)
        assert b.raw == pytest.approx(1.0, abs=1e-9)

    def test_unadjusted_split_would_poison_beta(self):
        """Documents WHY adjustment=split is an invariant: one unadjusted 10:1
        split manufactures a ~−90% weekly return and wrecks the regression."""
        _, mkt = correlated_pair(slope=1.0, noise=0.0)
        split_day = date(2025, 6, 2)
        raw_bars = [Bar(day=b.day, close=b.close if b.day < split_day
                        else b.close / 10.0) for b in mkt]
        b = compute_beta("BAD", raw_bars, mkt, START, END)
        assert abs(b.raw - 1.0) > 0.3                # beta visibly wrong…
        assert b.r_squared < 0.1                     # …and the fit destroyed
        # (the clean series above scores raw = 1.0 exactly with R² = 1.0)


# ── Alpaca adapter ───────────────────────────────────────────────────────────

SECRET = "SUPERSECRETKEY123"


def alpaca_with(handler) -> AlpacaClient:
    client = httpx.Client(base_url="https://data.alpaca.markets/v2",
                          headers={"APCA-API-KEY-ID": "KEYID",
                                   "APCA-API-SECRET-KEY": SECRET},
                          transport=httpx.MockTransport(handler))
    return AlpacaClient("KEYID", SECRET, client=client)


class TestAlpacaAdapter:
    def test_split_adjustment_requested_and_pagination_followed(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            params = dict(request.url.params)
            if "page_token" not in params:
                return httpx.Response(200, json={
                    "bars": [{"t": "2026-08-10T04:00:00Z", "c": 101.0},
                             {"t": "2026-08-11T04:00:00Z", "c": 102.0}],
                    "next_page_token": "tok2"})
            return httpx.Response(200, json={
                "bars": [{"t": "2026-08-12T04:00:00Z", "c": 103.0}],
                "next_page_token": None})

        bars = alpaca_with(handler).daily_bars("MSFT", date(2026, 8, 1), date(2026, 8, 12))
        assert [b.close for b in bars] == [101.0, 102.0, 103.0]
        assert len(seen) == 2
        for req in seen:                             # invariant: no raw-bar path
            assert req.url.params["adjustment"] == "split"
            assert req.headers["APCA-API-KEY-ID"] == "KEYID"

    def test_http_error_never_leaks_the_secret(self):
        def handler(request):
            return httpx.Response(500, text="boom")
        with pytest.raises(MarketDataError) as exc:
            alpaca_with(handler).daily_bars("MSFT", date(2026, 8, 1), date(2026, 8, 12))
        assert SECRET not in str(exc.value) and SECRET not in exc.value.user_message
        assert "500" in exc.value.user_message

    def test_unconfigured_credentials_fail_with_named_env_vars(self):
        with pytest.raises(MarketDataError) as exc:
            AlpacaClient("", "").daily_bars("MSFT", date(2026, 8, 1), date(2026, 8, 12))
        assert "ALPACA_API_KEY_ID" in exc.value.user_message


# ── FRED adapter ─────────────────────────────────────────────────────────────

class TestFredAdapter:
    def test_latest_non_null_observation_wins_percent_to_decimal(self):
        def handler(request):
            assert request.url.params["series_id"] == "DGS10"
            return httpx.Response(200, json={"observations": [
                {"date": "2026-08-12", "value": "."},          # holiday null
                {"date": "2026-08-11", "value": "4.28"},
                {"date": "2026-08-10", "value": "4.31"}]})
        client = httpx.Client(base_url="https://api.stlouisfed.org/fred",
                              transport=httpx.MockTransport(handler))
        value, as_of = FredClient("FREDKEY", client=client).dgs10()
        assert value == pytest.approx(0.0428)                  # decimal, not 4.28
        assert as_of == date(2026, 8, 11)

    def test_error_never_leaks_the_key(self):
        def handler(request):
            return httpx.Response(500, text="boom")
        client = httpx.Client(base_url="https://api.stlouisfed.org/fred",
                              transport=httpx.MockTransport(handler))
        with pytest.raises(MarketDataError) as exc:
            FredClient("FREDSECRETKEY", client=client).dgs10()
        assert "FREDSECRETKEY" not in str(exc.value)

    def test_all_null_observations_raise(self):
        def handler(request):
            return httpx.Response(200, json={"observations": [
                {"date": "2026-08-12", "value": "."}]})
        client = httpx.Client(base_url="https://api.stlouisfed.org/fred",
                              transport=httpx.MockTransport(handler))
        with pytest.raises(MarketDataError):
            FredClient("K", client=client).dgs10()


# ── Degradation ladder ───────────────────────────────────────────────────────

class BrokenVendor:
    """Vendor that always fails — forces the ladder past the live tier."""

    def daily_bars(self, symbol, start, end):
        raise MarketDataError("vendor down")

    def dgs10(self):
        raise MarketDataError("vendor down")


class WorkingVendor:
    def __init__(self):
        self.calls = 0

    def daily_bars(self, symbol, start, end):
        self.calls += 1
        return [Bar(day=date(2026, 8, 11), close=100.0),
                Bar(day=date(2026, 8, 12), close=101.0)]

    def dgs10(self):
        self.calls += 1
        return 0.0428, date(2026, 8, 11)


def age_cache(cache: SqliteCache, seconds: float) -> None:
    cache._conn.execute("UPDATE cache SET fetched_at = fetched_at - ?", (seconds,))
    cache._conn.commit()


class TestDegradationLadder:
    def make(self, tmp_path, vendor):
        cache = SqliteCache(tmp_path / "market.sqlite")
        provider = LadderedProvider(vendor, vendor, cache,
                                    snapshot_dir=tmp_path / "snaps")
        return provider, cache

    def test_live_then_fresh_cache(self, tmp_path):
        vendor = WorkingVendor()
        provider, _cache = self.make(tmp_path, vendor)
        _, tier = provider.get_bars("MSFT", date(2026, 8, 1), date(2026, 8, 12))
        assert tier == "live"
        provider.alpaca = BrokenVendor()             # vendor dies; cache serves
        bars, tier = provider.get_bars("MSFT", date(2026, 8, 1), date(2026, 8, 12))
        assert tier == "cache" and bars[-1].close == 101.0

    def test_stale_cache_when_live_down(self, tmp_path):
        provider, cache = self.make(tmp_path, WorkingVendor())
        provider.get_bars("MSFT", date(2026, 8, 1), date(2026, 8, 12))
        age_cache(cache, 3 * 24 * 3600)              # 3 days old — no longer fresh
        provider.alpaca = BrokenVendor()
        _, tier = provider.get_bars("MSFT", date(2026, 8, 1), date(2026, 8, 12))
        assert tier == "stale_cache"

    def test_snapshot_is_the_last_resort(self, tmp_path):
        provider, _ = self.make(tmp_path, BrokenVendor())
        snapdir = tmp_path / "snaps"
        snapdir.mkdir()
        (snapdir / "msft_bars.json.gz").write_bytes(gzip.compress(json.dumps(
            {"symbol": "MSFT", "bars": [["2026-08-01", 99.0]]}).encode()))
        bars, tier = provider.get_bars("MSFT", date(2026, 8, 1), date(2026, 8, 12))
        assert tier == "snapshot" and bars[0].close == 99.0
        price = provider.get_latest_price("MSFT")
        assert price.staleness == "snapshot" and price.value == 99.0

    def test_exhausted_ladder_raises(self, tmp_path):
        provider, _ = self.make(tmp_path, BrokenVendor())
        with pytest.raises(MarketDataUnavailableError):
            provider.get_bars("MSFT", date(2026, 8, 1), date(2026, 8, 12))

    def test_risk_free_ladder_mirrors_bars(self, tmp_path):
        provider, cache = self.make(tmp_path, WorkingVendor())
        rf = provider.get_risk_free()
        assert rf.staleness == "live" and rf.value == pytest.approx(0.0428)
        provider.fred = BrokenVendor()
        age_cache(cache, 3 * 24 * 3600)
        rf = provider.get_risk_free()
        assert rf.staleness == "stale_cache"         # rf moves slowly; stale is fine


# ── MarketInputs assembly ────────────────────────────────────────────────────

class TestBuildMarketInputs:
    def test_assembles_price_beta_rf_with_tiers(self):
        stock, mkt = correlated_pair(slope=1.2)
        provider = StaticMarketProvider({"SYN": stock, "SPY": mkt})
        mi = build_market_inputs("syn", provider, as_of=END)
        assert mi.ticker == "SYN"
        assert mi.price.value == pytest.approx(stock[-1].close)
        assert mi.price.staleness == "snapshot"
        assert mi.risk_free.value == pytest.approx(0.042)
        assert mi.beta is not None
        assert mi.beta.raw == pytest.approx(1.2, abs=0.05)
        assert mi.beta.staleness == "snapshot"
        assert mi.warnings == []

    def test_short_history_beta_falls_back_with_loud_warning(self):
        stock, mkt = correlated_pair()
        recent = END - timedelta(days=250)           # a recent IPO
        provider = StaticMarketProvider(
            {"IPO": [b for b in stock if b.day >= recent], "SPY": mkt})
        mi = build_market_inputs("IPO", provider, as_of=END)
        assert mi.beta is None
        assert [w.code for w in mi.warnings] == ["beta_fallback"]
        assert "1.0" in mi.warnings[0].message

    def test_benchmark_unavailable_degrades_not_blocks(self):
        stock, _ = correlated_pair()
        provider = StaticMarketProvider({"SYN": stock})      # no SPY anywhere
        mi = build_market_inputs("SYN", provider, as_of=END)
        assert mi.beta is None
        assert [w.code for w in mi.warnings] == ["benchmark_unavailable"]

    def test_missing_price_is_fatal(self):
        _, mkt = correlated_pair()
        provider = StaticMarketProvider({"SPY": mkt})
        with pytest.raises(MarketDataUnavailableError):
            build_market_inputs("GONE", provider, as_of=END)


# ── Frozen fixture + live smoke ──────────────────────────────────────────────

@pytest.mark.skipif(not (SNAPSHOT_DIR / "msft_beta_frozen.json").exists(),
                    reason="market snapshots not captured yet — run: "
                           "python -m market.snapshot MSFT KO COST KHC")
def test_msft_beta_frozen_against_methodology_drift():
    """MSFT's beta from the committed bars is frozen at capture time
    (market.snapshot writes msft_beta_frozen.json); a diff here means the beta
    methodology moved, which must be a deliberate, reviewed change."""
    provider = LadderedProvider(BrokenVendor(), BrokenVendor(), cache=NullCache())
    bars, tier = provider.get_bars("MSFT", date(2000, 1, 1), date(2100, 1, 1))
    assert tier == "snapshot"
    spy, _ = provider.get_bars("SPY", date(2000, 1, 1), date(2100, 1, 1))
    b = compute_beta("MSFT", bars, spy, bars[0].day, bars[-1].day)
    frozen = json.loads((SNAPSHOT_DIR / "msft_beta_frozen.json").read_text())
    assert b.raw == pytest.approx(frozen["raw"], abs=1e-9)
    assert b.n_obs == frozen["n_obs"]


@pytest.mark.skipif(not os.environ.get("ALPACA_API_KEY_ID")
                    or not os.environ.get("FRED_API_KEY"),
                    reason="live smoke test needs ALPACA_API_KEY_ID + FRED_API_KEY")
def test_live_smoke():
    from market.provider import market_today
    alpaca = AlpacaClient(os.environ["ALPACA_API_KEY_ID"],
                          os.environ["ALPACA_API_SECRET_KEY"])
    today = market_today()
    bars = alpaca.daily_bars("MSFT", today - timedelta(days=30), today)
    assert bars and bars[-1].close > 0
    value, _ = FredClient(os.environ["FRED_API_KEY"]).dgs10()
    assert 0.0 < value < 0.15
