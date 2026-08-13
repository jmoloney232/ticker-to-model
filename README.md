# Ticker to Model

Enter a US public-company ticker → get an editable set of financial assumptions, a DCF
valuation dashboard, and a downloadable Excel workbook containing a full three-statement
model with **live formulas** (not pasted values).

> **Status:** specification phase. The repo currently contains the project specs
> (`specs/`), the canonical data schema (`backend/ingest/schema.yaml`), and the valuation
> methodology (`backend/engine/methodology.yaml`). Application code lands module by
> module in the build order below.

## How it works

```
ticker ─► ingest (SEC EDGAR XBRL → validated history)
              ─► engine (assumptions → projections → DCF)  ◄─ market data (Alpaca, FRED)
                    ─► dashboard (React)
                    ─► Excel workbook (openpyxl, formula-driven)
```

- **Ingest** pulls every XBRL fact the company has filed from EDGAR `companyfacts`, maps
  inconsistent tags through documented fallback chains, resolves restatements
  (latest-filed wins), and refuses to proceed if the statements don't tie.
- **Engine** derives every assumption default from the company's own history (documented
  derivation rules, all user-overridable) and produces projections, unlevered FCF, WACC,
  a DCF with two terminal-value methods, and sensitivity grids. Pure functions, no I/O.
- **Excel writer** emits a workbook where every output cell is a formula referencing
  inputs and named ranges — click FY29 EBITDA and see the calculation, not a number.
- **Web app** is the front door: ticker in, dashboard + assumptions panel + download out,
  including a Methodology page documenting every financial convention used.

## Repo map

| Path | What |
|---|---|
| `specs/` | Authoritative module specs (start with `specs/00-overview.md`) |
| `backend/ingest/` | EDGAR client, tag mapping, period selection (+ `schema.yaml`) |
| `backend/engine/` | Valuation engine (+ `methodology.yaml`) |
| `backend/excel/` | Workbook writer |
| `backend/market/` | Market-data provider interface (Alpaca, FRED, beta) |
| `backend/app/` | FastAPI layer |
| `frontend/` | React/TypeScript UI |
| `backend/tests/fixtures/` | Committed EDGAR/market snapshots for the fixture tickers |

## Scope (v1)

Large US non-financial public companies, annual periods, 5-year explicit forecast, DCF
with perpetuity-growth and exit-multiple terminal values, sensitivity analysis. Banks,
insurers, and REITs are detected and rejected with a clear message. See
`backend/engine/methodology.yaml` for every valuation convention and its rationale.
