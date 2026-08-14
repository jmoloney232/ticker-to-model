# Spec 06 — Web app (API + frontend)

Thin FastAPI layer over ingest/market/engine/excel, plus the React frontend. Neither
computes anything. Deploy: API on Render (long-lived service), frontend static on
Vercel. API keys live server-side only; the browser talks exclusively to our API.

## Inputs

- HTTP requests (ticker, assumption overrides)
- Module outputs: `FinancialHistory`, `MarketInputs`, `ModelResult`, workbook bytes
- `methodology.yaml`

## Outputs — API

| Endpoint | Returns |
|---|---|
| `GET /api/health` | Liveness + degradation status per data source |
| `GET /api/methodology` | methodology.yaml rendered as JSON (single source of truth for the /methodology page and anything else that lists conventions) |
| `GET /api/company/{ticker}` | Full model at default assumptions (see response shape) |
| `POST /api/company/{ticker}/model` | Body: `{overrides: {field: value}}` → recomputed model |
| `POST /api/company/{ticker}/workbook` | Same body → `.xlsx` stream (`Content-Disposition: attachment; TICKER_model_YYYY-MM-DD.xlsx`) |

Response shape (company endpoints): company meta · history summary (per-year statements
for display) · `assumptions`: per field `{default, derivation, override, effective,
unit, group, bounds}` · `model`: the full ModelResult · `validation`: spec 07 report ·
`warnings` · `staleness`: per-source tier + as-of dates.

Error envelope: `{error: {code, message, detail?}}` with the ingest/engine error taxonomy
mapped to HTTP: UnknownTicker → 404 · FinancialCompany / UnsupportedCurrency /
InsufficientHistory / MissingRequiredItem / InvalidAssumption → 422 with the specific
message · Validation fail → 422 with the tie-out detail · EdgarUnavailable (cold ticker,
all tiers exhausted) → 503 with the friendly retry message. **Third-party outages never
produce a 500** — that path is the degradation ladder (spec 00), and `/api/health`
exposes which tier each source is running on.

Ops: CORS pinned to the frontend origin · modest per-IP rate limit on company endpoints
(they fan out to EDGAR) · overrides validated against per-field bounds server-side.

## Outputs — frontend

Routes:

- `/` — ticker input; example tickers; clear rejection messages (bank/insurer/REIT)
  rendered as first-class content, not error styling.
- `/company/:ticker` — the dashboard:
  - **Header:** value per share (both TV methods) vs current price, upside/downside.
  - **Validation banner:** green "statements tie" / yellow warnings / red fail —
    always visible, never buried (non-negotiable #3).
  - **Assumptions panel:** grouped (Growth · Margins · Working capital · Capital
    intensity · Taxes · WACC · Terminal value · Conventions). Every field shows its
    default, its derivation sentence ("3-yr avg DSO of 47 days"), and highlights
    overrides with a per-field and global reset. Edits debounce → `POST /model` →
    dashboard updates. Toggles (mid-year, SBC, Kd method, beta) shown with one-line
    tradeoffs and a link to /methodology.
  - **Charts:** EV→equity bridge waterfall · historical + projected revenue/margins ·
    UFCF schedule · sensitivity heat grids (both) · implied cross-check callouts
    ("your 12.0× exit implies g = 3.1%").
  - **Warnings list:** restatements, 53-week years, lease_heavy, beta fallback,
    staleness chips ("price as of …", "financials cached …").
  - **Download** button → workbook with current overrides baked in as the workbook's
    input cells.
- `/methodology` — every convention from `GET /api/methodology`: default, derivation
  rule, tradeoff, which surfaces use it. This page is a **product requirement**
  (owner): everything financial used to build the models must be navigable here.
  **Assumption presets render automatically from `engine/presets.yaml`** (name,
  rationale, per-field rules) — adding a preset requires no code change and no
  separate methodology edit; the page reads the file (owner contract, 2026-08-14).

Degraded states (each designed, not accidental): market-data-down → historicals +
assumptions shown, DCF section replaced with reason card · stale-cache → chips ·
cold ticker + EDGAR down → friendly full-page retry state · validation fail → red
banner + detail table, no model shown.

## Invariants

- No secrets, vendor names, or vendor payloads reach the browser.
- Frontend renders only backend-computed numbers (it never re-implements model math).
- Every degraded state has an explicit design; an unstyled error page is a bug.
- Overrides round-trip: what the dashboard shows equals what the workbook download
  contains.

## Error cases

Covered by the envelope table above; frontend maps each `code` to a designed state.
Unknown/unmapped error codes render the generic friendly failure card (and log).

## How tested

- **API contract tests** against a fixture-backed app (no live third-party calls):
  every endpoint, every error mapping, the degradation tiers via mocked providers,
  override validation (in-bounds, out-of-bounds, unknown field), workbook endpoint
  returns valid xlsx with overrides applied.
- **Schema snapshots** of API responses per fixture ticker (drift = deliberate change).
- **Frontend:** v1 keeps it light — component tests for the assumptions panel
  (default/override/reset logic) and one Playwright happy path: search MSFT → see
  dashboard → change growth → value updates → download workbook. Degraded states
  exercised via mocked API responses.
