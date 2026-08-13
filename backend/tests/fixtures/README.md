# fixtures — committed real-data snapshots

One directory per fixture ticker (`msft/`, `ko/`, `cost/`, `khc/`, `jpm/`), each holding
`companyfacts.json.gz` and `submissions.json.gz`, plus the shared
`company_tickers.json.gz` and `manifest.json` (ticker → CIK, used by the snapshot
fallback tier). Captured by `python -m ingest.snapshot MSFT KO COST KHC JPM` with
`EDGAR_USER_AGENT` set; refreshed deliberately, never automatically.

These are both test inputs (`tests/test_fixtures_real.py`) and the final fallback tier
of the degradation ladder (spec 00).

**Pruning** (keeps the repo small; still 100% real filed data):
- `us-gaap` facts: 10-K / 10-K/A forms only (annual scope)
- `dei` facts: all forms kept — the current cover-page share count comes from the
  latest 10-Q
- units: USD, shares, USD/shares only; `submissions` stripped of its filing index

Last captured: 2026-08-13.
