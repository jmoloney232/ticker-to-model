"""EDGAR HTTP client (spec 01 §1–3).

Etiquette (CLAUDE.md): User-Agent with contact info on every request, global
token-bucket rate limit at 8 req/s (headroom under SEC's 10), retries with backoff,
then the degradation ladder: live -> fresh cache -> stale cache -> committed
snapshot -> EdgarUnavailableError.

The `EdgarSource` protocol is the seam tests use to inject fixture-backed data
without HTTP.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
from pathlib import Path
from typing import Protocol

import httpx

from .cache import NullCache
from .errors import EdgarUnavailableError, UnknownTickerError
from .models import Tier

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

FRESH_TTL_S = 24 * 3600
MAX_ATTEMPTS = 3
RETRY_STATUS = {429, 500, 502, 503, 504}


class EdgarSource(Protocol):
    def get_company_tickers(self) -> tuple[dict, Tier]: ...
    def get_submissions(self, cik: int) -> tuple[dict, Tier]: ...
    def get_companyfacts(self, cik: int) -> tuple[dict, Tier]: ...


class RateLimiter:
    """Global minimum spacing between requests (8/s => 125ms)."""

    def __init__(self, per_second: float = 8.0):
        self._min_interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self._last + self._min_interval - now
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last = time.monotonic()


class EdgarClient:
    def __init__(
        self,
        user_agent: str,
        cache=None,
        fixture_dir: str | Path | None = None,
        rate_limiter: RateLimiter | None = None,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not user_agent or "@" not in user_agent:
            raise ValueError(
                "EDGAR_USER_AGENT must identify you with contact info, e.g. "
                '"TickerToModel/0.1 (you@example.com)" — refusing to start without it.'
            )
        self._cache = cache or NullCache()
        self._fixture_dir = Path(fixture_dir) if fixture_dir else None
        self._limiter = rate_limiter or RateLimiter()
        self._http = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip"},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )

    # ── public API ─────────────────────────────────────────────────────────
    def get_company_tickers(self) -> tuple[dict, Tier]:
        return self._get("tickers", TICKERS_URL, snapshot_rel="company_tickers.json.gz")

    def get_submissions(self, cik: int) -> tuple[dict, Tier]:
        return self._get(f"submissions:{cik}", SUBMISSIONS_URL.format(cik=cik),
                         snapshot_rel=self._snapshot_rel(cik, "submissions.json.gz"))

    def get_companyfacts(self, cik: int) -> tuple[dict, Tier]:
        return self._get(f"companyfacts:{cik}", COMPANYFACTS_URL.format(cik=cik),
                         snapshot_rel=self._snapshot_rel(cik, "companyfacts.json.gz"))

    def resolve_cik(self, ticker: str) -> int:
        ticker = ticker.strip().upper()
        payload, _ = self.get_company_tickers()
        for row in payload.values():
            if row["ticker"].upper() == ticker:
                return int(row["cik_str"])
        raise UnknownTickerError(ticker)

    # ── degradation ladder ─────────────────────────────────────────────────
    def _get(self, key: str, url: str, snapshot_rel: str | None) -> tuple[dict, Tier]:
        cached = self._cache.get(key, FRESH_TTL_S)
        if cached is not None and cached[1]:
            return cached[0], "cache"

        try:
            payload = self._fetch(url)
        except (httpx.HTTPError, OSError):
            payload = None

        if payload is not None:
            self._cache.put(key, payload)
            return payload, "live"

        if cached is not None:                      # stale but real
            return cached[0], "stale_cache"

        snap = self._load_snapshot(snapshot_rel)
        if snap is not None:
            return snap, "snapshot"

        raise EdgarUnavailableError(key)

    def _fetch(self, url: str) -> dict:
        last_exc: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            self._limiter.wait()
            try:
                resp = self._http.get(url)
                if resp.status_code in RETRY_STATUS:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, OSError) as exc:
                last_exc = exc
                time.sleep(0.5 * (2 ** attempt))
        raise last_exc  # type: ignore[misc]

    # ── committed snapshots (tier 3) ───────────────────────────────────────
    def _snapshot_rel(self, cik: int, filename: str) -> str | None:
        if self._fixture_dir is None:
            return None
        manifest = self._fixture_dir / "manifest.json"
        if not manifest.exists():
            return None
        mapping = json.loads(manifest.read_text())   # {ticker: cik}
        for ticker, mapped_cik in mapping.items():
            if int(mapped_cik) == cik:
                return f"{ticker.lower()}/{filename}"
        return None

    def _load_snapshot(self, rel: str | None) -> dict | None:
        if self._fixture_dir is None or rel is None:
            return None
        path = self._fixture_dir / rel
        if not path.exists():
            return None
        return json.loads(gzip.decompress(path.read_bytes()))


class StaticSource:
    """In-memory EdgarSource for tests: hand it the three payloads directly."""

    def __init__(self, tickers: dict, submissions: dict, companyfacts: dict,
                 tier: Tier = "live"):
        self._tickers = tickers
        self._submissions = submissions
        self._companyfacts = companyfacts
        self._tier: Tier = tier

    def get_company_tickers(self) -> tuple[dict, Tier]:
        return self._tickers, self._tier

    def get_submissions(self, cik: int) -> tuple[dict, Tier]:
        return self._submissions, self._tier

    def get_companyfacts(self, cik: int) -> tuple[dict, Tier]:
        return self._companyfacts, self._tier

    def resolve_cik(self, ticker: str) -> int:
        ticker = ticker.strip().upper()
        for row in self._tickers.values():
            if row["ticker"].upper() == ticker:
                return int(row["cik_str"])
        raise UnknownTickerError(ticker)
