# tests

Pytest suite. `fixtures/` holds committed real-data snapshots (EDGAR `companyfacts`,
Alpaca bars, FRED DGS10) for the five fixture tickers — MSFT, KO, COST, KHC, JPM — which
double as the last-resort graceful-degradation fallback. Each spec's "How tested"
section defines what belongs here.
