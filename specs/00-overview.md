# Spec 00 — System overview

Read this first. Each module has its own spec with the same five sections: **Inputs,
Outputs, Invariants, Error cases, How tested.** Specs are authoritative; when code and a
spec disagree, one of them gets fixed deliberately.

## Dataflow

```
                       ┌────────────────────────────────────────────┐
 ticker ──────────────►│ ingest (spec 01)                           │
                       │  EDGAR companyfacts + submissions          │
                       │  tag mapping via schema.yaml (spec 02)     │
                       │  period selection, restatement resolution  │
                       │  validation gate (spec 07)                 │
                       └───────────────┬────────────────────────────┘
                                       │ FinancialHistory
                                       ▼
 ┌───────────────────┐   ┌────────────────────────────────────────┐
 │ market (spec 03)  │──►│ engine (spec 04)                       │
 │  Alpaca bars      │   │  assumption defaults (derivation rules)│
 │  FRED DGS10       │   │  3-statement projections               │
 │  beta (2y weekly) │   │  FCF → WACC → DCF → sensitivities      │
 └───────────────────┘   │  conventions from methodology.yaml     │
     MarketInputs        └───────┬──────────────────┬─────────────┘
                                 │ ModelResult      │ ModelResult
                                 ▼                  ▼
                  ┌──────────────────────┐   ┌─────────────────────────┐
                  │ excel (spec 05)      │   │ app + frontend (spec 06)│
                  │  formula workbook    │   │  dashboard, assumptions │
                  │  named ranges        │   │  panel, /methodology,   │
                  │  parity-tested       │   │  download               │
                  └──────────────────────┘   └─────────────────────────┘
```

## Module boundaries

- `ingest` knows EDGAR and XBRL; it knows nothing about valuation.
- `market` knows vendors (Alpaca, FRED) behind one `MarketDataProvider` interface; it
  knows nothing about EDGAR or valuation.
- `engine` is pure functions: `(FinancialHistory, MarketInputs, Assumptions) →
  ModelResult`. No I/O of any kind. It is the single source of truth for all numbers.
- `excel` renders a `ModelResult` as a formula-driven workbook. It re-expresses engine
  math as Excel formulas; a parity test (spec 05) keeps the two from drifting.
- `app` is a thin FastAPI layer; `frontend` is the React UI. Neither computes anything.

Two data files are load-bearing:

- `backend/ingest/schema.yaml` — canonical line items and XBRL fallback tag chains
  (human-readable mirror: spec 02).
- `backend/engine/methodology.yaml` — every valuation convention, default, derivation
  rule, and tradeoff. Rendered as the website's `/methodology` page and the workbook's
  Methodology sheet. One source, three surfaces.

## Degradation ladder (non-negotiable #4)

For every external call, in order:

1. **Live API** (EDGAR / Alpaca / FRED)
2. **SQLite runtime cache** — last successful response, with a staleness annotation
   surfaced to the user ("financials as of …", "price as of …")
3. **Committed snapshot** (`backend/tests/fixtures/`) — guaranteed present for fixture
   tickers
4. **Graceful partial state** — the one legitimate dead end: a never-fetched ticker
   while EDGAR is down → friendly "data source unavailable, try later" screen (never a
   stack trace or error page). If only *market* data is down, the app still shows
   historicals and assumptions and marks the DCF as unavailable-with-reason.

Degraded results are always labeled. A model built from stale data says so.

## Glossary

| Term | Meaning here |
|---|---|
| Fact | One XBRL-tagged value from `companyfacts` (value + period + unit + accession) |
| Duration / instant | Fact shapes: flows have start+end dates (IS/CF); stocks have one date (BS) |
| Accession | Unique ID of one SEC filing; restatements = same period, later accession |
| Canonical item | Our normalized line item (e.g. `revenue`), mapped from a tag chain |
| Tag chain | Ordered list of us-gaap/dei tags tried in sequence for one canonical item |
| FinancialHistory | Validated multi-year statement set + provenance + warnings (ingest output) |
| MarketInputs | Price, share info, beta, risk-free rate (market output) |
| Assumptions | Editable inputs with derived defaults (engine input) |
| ModelResult | Projections, FCF schedule, WACC, DCF, bridge, sensitivities (engine output) |
| Tie-out | A cross-statement identity that must hold (spec 07) |

## Fixture tickers (used across all specs' tests)

MSFT (clean, June FYE) · KO (clean, calendar FYE) · COST (52/53-week retailer, lease
warning) · KHC (restatement) · JPM (bank → clean rejection). Rationale in spec 01.

## Error philosophy

Errors are typed, not stringly. Every module defines its error taxonomy in its spec.
User-facing failures always say *what* failed, *why*, and *what still works*. Silent
fallback to wrong-but-plausible numbers is the worst failure mode this project can have —
prefer loud partial results over quiet complete ones.
