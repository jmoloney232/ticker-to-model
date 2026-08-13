# Spec 03 — Market data

All market data flows through one `MarketDataProvider` interface. Vendors (Alpaca, FRED)
are adapter modules behind it; swapping a vendor means replacing one module. Also owns
the in-house beta computation. Knows nothing about EDGAR or valuation.

## Inputs

- Symbol(s), date ranges, from callers
- Env: `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `FRED_API_KEY` (server-side only)
- SQLite cache handle

## Outputs

`MarketInputs` for a company:

- `price`: latest close (split-adjusted), with `as_of` date and `staleness` tier
  (live / cached / snapshot)
- `beta`: raw OLS beta, Blume-adjusted beta, regression metadata (n_obs, window,
  frequency, benchmark, R²)
- `risk_free`: 10Y Treasury yield (decimal), `as_of` date, staleness tier
- Every field carries its degradation tier so the UI and workbook can label staleness.

## Interface

```
MarketDataProvider (protocol)
  get_bars(symbol, start, end, freq="1D") -> Bars   # ALWAYS split-adjusted
  get_latest_price(symbol) -> PricePoint
  get_risk_free() -> RatePoint
```

Adapters: `alpaca.py` (bars, prices — request `adjustment=split`), `fred.py` (DGS10,
latest non-null observation, percent → decimal). The engine consumes `MarketInputs`
only; it never sees a vendor name.

## Beta computation (owner-reviewed convention)

- **Window/frequency: 2 years of weekly returns** vs **SPY**. Weekly avoids the
  non-synchronous-trading bias that pulls daily betas toward zero below mega-cap
  liquidity; 2y keeps recency. (Bloomberg default is 2y weekly; Damodaran uses 5y
  monthly.)
- Weekly return = simple return between last trading closes of consecutive weeks
  (ISO weeks; both series sampled on the same dates — intersection of trading days).
- OLS slope of stock weekly returns on SPY weekly returns. Target ~104 paired
  observations; **minimum 80**, else `InsufficientPriceHistoryError` (recent IPOs).
- **Blume adjustment** `β_adj = (2/3)·β_raw + 1/3` applied by default; both raw and
  adjusted reported; the engine's toggle (methodology: `beta_adjustment`) picks one.
- **Disclosed caveat** (methodology page): a regression beta embeds the company's
  current leverage; if projections materially change capital structure the beta is
  silently inconsistent. Unlever/relever is a documented v2 extension.

## Caching

- **Benchmark bars (SPY) are cached globally** — identical across companies; one
  refresh serves everyone. Refreshed when >24h old.
- Per-symbol bars and prices cached in SQLite keyed (symbol, range, freq).
- DGS10 cached; refreshed when >24h old.
- Committed snapshots for fixture tickers (bars for MSFT/KO/COST/KHC + SPY, one DGS10
  observation) are the last-resort tier and the test fixtures.

## Degradation (per source, independent)

| Failure | Behavior |
|---|---|
| Alpaca down, cache warm | Cached price/bars with staleness label |
| Alpaca down, cold, snapshot exists | Snapshot with "as of {date}" label |
| Alpaca down, cold, no snapshot | `MarketDataUnavailable`: app still shows historicals + assumptions; DCF marked unavailable-with-reason (spec 00 partial state) |
| FRED down | Cached/snapshot DGS10 with staleness label (rf moves slowly; stale is fine, labeled) |
| Split detected mid-cache | Bars are always re-requested split-adjusted; cache invalidated on adjustment mismatch (see tests) |

## Invariants

- Bars are split-adjusted, always — a raw-bar code path must not exist.
- Stock and benchmark return series are index-aligned before regression (no silent
  forward-fill; missing weeks drop the pair).
- All rates/returns are decimals internally (0.042, never 4.2).
- No vendor type leaks past the adapter boundary.
- Keys never appear in logs, errors, or client-bound payloads.

## Error cases

| Error | Trigger | Behavior |
|---|---|---|
| `InsufficientPriceHistoryError` | <80 paired weekly obs | Beta unavailable → engine falls back to β=1.0 with a loud warning ("market-average beta assumed"), user-overridable |
| `MarketDataUnavailable` | All tiers exhausted for a required field | Partial state per spec 00 |
| `BenchmarkUnavailable` | SPY series unavailable in all tiers | Same as above (beta needs the benchmark) |

## How tested

- **Synthetic regression tests:** constructed return series with known slope (e.g.
  stock = 1.3×SPY + noise) → recovered raw beta within tolerance; Blume formula exact.
- **Split handling:** synthetic bar series across a 10:1 split — adjusted series shows
  no fake return; a deliberately unadjusted series fails the guard test.
- **Alignment:** series with mismatched holidays/missing weeks → pairs dropped, n_obs
  correct, no forward-fill.
- **Degradation ladder:** each row of the table above simulated with a mock provider +
  cache states; assert labels and partial states.
- **Fixture snapshots:** beta for MSFT computed from committed bars is snapshot-frozen
  (regression test against accidental methodology drift).
- **Live smoke test** (marked, non-CI): one Alpaca and one FRED call.
