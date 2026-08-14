"""FRED adapter — DGS10, the 10Y Treasury constant-maturity yield.

Vendor-facing only; the ladder lives in provider.py. FRED reports percent with
"." for market holidays — the latest non-null observation wins, converted to a
decimal (invariant: rates are decimals internally, 0.042 never 4.2).

The API key travels as a query parameter, so error paths NEVER include the URL.
"""

from __future__ import annotations

from datetime import date

import httpx

from .errors import MarketDataError

BASE_URL = "https://api.stlouisfed.org/fred"


class FredClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self._configured = bool(api_key)
        self._api_key = api_key
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=30.0)

    def dgs10(self) -> tuple[float, date]:
        """Latest non-null DGS10 observation as (decimal rate, observation date)."""
        if not self._configured:
            raise MarketDataError("FRED credentials are not configured (FRED_API_KEY).")
        try:
            resp = self._client.get("/series/observations", params={
                "series_id": "DGS10",
                "api_key": self._api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 10,               # a week of holidays is 5 nulls; 10 is safe
            })
        except httpx.HTTPError as exc:
            # the URL carries the key — name the failure class, nothing else
            raise MarketDataError(
                f"FRED request failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise MarketDataError(f"FRED returned HTTP {resp.status_code}.")
        for obs in resp.json().get("observations", []):
            if obs.get("value") not in (None, "", "."):
                return float(obs["value"]) / 100.0, date.fromisoformat(obs["date"])
        raise MarketDataError("FRED returned no non-null DGS10 observations.")
