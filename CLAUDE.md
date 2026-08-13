# CLAUDE.md — Ticker to Model

## What this is

A web app: user enters a US public-company ticker → editable financial assumptions → DCF
valuation dashboard → downloadable Excel workbook containing a full three-statement model
with live formulas. **Audience: recruiters** (consulting, PM, fintech). The project must
survive two hostile reviews: a finance-literate person clicking into Excel cells, and an
engineer reading this repo. The owner's background is accounting (strong) and finance
(weaker) — when a valuation convention is ambiguous, flag it and explain the tradeoff;
never silently pick one. Every flagged convention lives in
`backend/engine/methodology.yaml`.

## Non-negotiables (priority order)

1. **The Excel export contains live formulas, not computed values.** Clicking FY29
   EBITDA must show a formula referencing other cells. Inputs, calculations, and outputs
   visually and structurally separated; named ranges for all assumptions. This is the
   single most important requirement.
2. **Assumptions are editable inputs, not fixed outputs.** Every default derives from the
   company's own reported history via a documented derivation rule; every one is
   user-overridable.
3. **Validation is a first-class feature.** Balance sheet must balance, cash flow must
   tie to the change in cash, net income must agree across statements. Failures surface
   loudly. Never silently ship a model that doesn't tie.
4. **Graceful degradation.** External API failure → cached data → committed snapshot →
   a complete model anyway. Never an error page because a third party is down. (One
   carve-out: a never-before-fetched ticker while EDGAR is down gets a graceful
   "unavailable, try later" state — there is nothing to fall back to.)
5. **Methodology page.** Every financial convention, default, and derivation rule used
   anywhere in the model is user-navigable on the website, rendered from
   `backend/engine/methodology.yaml` (also rendered as the workbook's Methodology sheet).

## Build order and status

Build and verify each phase before starting the next.

| Phase | Module | Status |
|---|---|---|
| 0 | Specs, schema, methodology | done (owner-reviewed) |
| 1 | `backend/ingest/` — EDGAR fetch, tag mapping, periods, validation | **done — 74 tests incl. real-fixture suite; awaiting owner review** |
| 2 | `backend/engine/` + `backend/market/` — projections, WACC, DCF | not started |
| 3 | `backend/excel/` — formula-driven workbook | not started |
| 4 | `backend/app/` + `frontend/` — API, dashboard, download | not started |

Dev: `cd backend && .venv/bin/python -m pytest` (venv via `python3 -m venv .venv` +
`pip install httpx pyyaml pytest ruff`). Fixture refresh:
`EDGAR_USER_AGENT=... python -m ingest.snapshot MSFT KO COST KHC JPM`.

## Architecture

```
ticker ─► ingest (EDGAR companyfacts ─ schema.yaml tag chains ─ validation)
             │ FinancialHistory
             ▼
          engine (assumptions defaults ─ projections ─ FCF ─ WACC ─ DCF ─ sensitivities)
             │ ▲ MarketInputs (price, beta, rf) from market/
             │ ModelResult
             ├────────► excel writer ─► .xlsx (formulas + named ranges)
             └────────► app (FastAPI) ─► frontend (React) dashboard + /methodology
```

Specs in `specs/` are authoritative — one per module, each with Inputs / Outputs /
Invariants / Error cases / How tested. **When code and spec disagree, fix one
deliberately; never let them drift silently.**

## Conventions

- **Engine purity:** `backend/engine/` contains pure functions. No I/O, no HTTP, no
  filesystem, no framework imports. It may import typed dataclasses only.
- **Provider interface:** all market data flows through the `MarketDataProvider`
  interface in `backend/market/`. Swapping Alpaca means replacing one adapter module.
- **EDGAR etiquette:** `User-Agent` from `EDGAR_USER_AGENT` env var on every request;
  global rate limit under 10 req/s.
- **Market data:** split-adjusted bars only (raw bars turn a split into a fake return).
- **Secrets:** API keys in env vars, server-side only, never committed, never sent to or
  used from the browser.
- **No silent zeros:** an unmapped or missing line item is logged (optional items) or a
  hard error (required items) — never defaulted to 0 without a documented per-item rule.
- **Provenance everywhere:** every ingested number carries the tag it came from, the
  accession, and whether it was restated.
- **Python 3.12, uv, ruff, pytest** in `backend/`; Vite + React + TS in `frontend/`.

## Decision log (rationale in specs and methodology.yaml)

- **Deploy:** FastAPI on Render (long-lived service; serverless is a poor fit for
  pandas/openpyxl + slow EDGAR fetches); static React frontend on Vercel.
- **Cache:** SQLite runtime cache + committed snapshots for fixture tickers in
  `backend/tests/fixtures/` (snapshots double as last-resort fallback).
- **Restatements:** latest-filed accession wins (as-restated basis — correct for
  forecasting); warn when the value differs >1% from originally filed.
- **Fiscal calendars:** annual periods identified by ~365-day durations anchored to the
  filer's FYE; 53-week years (≥371 days) detected and annotated, not normalized.
- **Projection mechanics:** cash is the balance-sheet plug (no revolver in v1); interest
  computed on beginning-of-period balances so the exported workbook never needs
  iterative calculation (no circular references — hard requirement).
- **Engine is source of truth for numbers:** the Excel writer mirrors engine math and a
  CI parity test recalculates the workbook and diffs every output cell against the
  engine.
- **SBC:** expensed in FCF by default (no add-back); documented toggle. Owner-confirmed.
- **Share count:** current cover-page basic shares × latest diluted/basic weighted-average
  ratio (TSM approximation — footnote option data is out of scope). Owner-reviewed.
- **Beta:** 2y weekly OLS vs SPY, Blume-adjusted by default; leverage caveat disclosed.
- **Terminal g:** default `max(1.5%, min(2.5%, 10Y))`; overrides warn; `g ≥ WACC` blocks.
- **Terminal value:** Gordon leg uses reinvestment consistency `RR = g / ROIC_t`;
  exit-multiple leg cross-checked via implied g ↔ implied multiple (no comps needed).
- **Cost of debt:** synthetic rating (coverage → spread) by default; embedded-coupon
  toggle. After-taxed at the same marginal rate used in terminal NOPAT.
- **WACC weights:** gross book debt + equity at market cap. Net debt appears only in the
  EV→equity bridge. Tested invariant.
- **Operating leases:** excluded from net debt (consistent with unadjusted EBITDA);
  warning surfaced when lease liability > 25% of total debt.

## Scope guardrails (v1)

Large US non-financial companies, annual periods, 5-year forecast. Banks, insurers,
REITs: detect by SIC code and reject with a clear message — never produce garbage. Out
of scope (architect for, don't build): segments, footnotes, MD&A, quarterly data, comps,
LBO. Fixture tickers: MSFT (clean, June FYE), KO (clean, calendar FYE), COST (52/53-week
retailer), KHC (restatement), JPM (bank → rejection).
