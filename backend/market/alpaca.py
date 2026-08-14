"""Alpaca adapter — split-adjusted daily bars (specs/03-market-data.md).

Vendor-facing only: raises MarketDataError on any failure; the degradation
ladder lives in provider.py. Raw (unadjusted) bars have no code path here —
`adjustment=split` is hardcoded, because a raw bar turns a stock split into a
fake single-day return that would destroy the beta regression (invariant).

Keys travel in headers and never appear in errors or logs.
"""

from __future__ import annotations

from datetime import date, datetime

import httpx

from .errors import MarketDataError
from .models import Bar

BASE_URL = "https://data.alpaca.markets/v2"
PAGE_LIMIT = 10_000


class AlpacaClient:
    def __init__(self, key_id: str, secret_key: str,
                 client: httpx.Client | None = None, feed: str = "iex"):
        self._configured = bool(key_id and secret_key)
        self.feed = feed
        self._client = client or httpx.Client(
            base_url=BASE_URL,
            headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key},
            timeout=30.0,
        )

    def daily_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        """All split-adjusted daily bars for [start, end], paginated."""
        if not self._configured:
            raise MarketDataError(
                "Alpaca credentials are not configured "
                "(ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY).")
        bars: list[Bar] = []
        page_token: str | None = None
        while True:
            params = {
                "timeframe": "1Day",
                "adjustment": "split",       # invariant: no raw-bar code path
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": PAGE_LIMIT,
                "feed": self.feed,
            }
            if page_token:
                params["page_token"] = page_token
            payload = self._get(f"/stocks/{symbol}/bars", params, symbol)
            for row in payload.get("bars") or []:
                day = datetime.fromisoformat(row["t"]).date()
                bars.append(Bar(day=day, close=float(row["c"])))
            page_token = payload.get("next_page_token")
            if not page_token:
                return bars

    def _get(self, path: str, params: dict, symbol: str) -> dict:
        try:
            resp = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            # never echo the request (headers hold keys); name the failure class only
            raise MarketDataError(
                f"Alpaca request for {symbol} failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise MarketDataError(
                f"Alpaca returned HTTP {resp.status_code} for {symbol}.")
        return resp.json()
