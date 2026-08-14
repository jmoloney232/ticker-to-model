# Spec 06 — Web app (API + frontend)

Thin FastAPI layer over ingest/market/engine/excel, plus the React frontend. Neither
computes anything. Deploy: API on Render (long-lived service), frontend static on
Vercel. API keys live server-side only; the browser talks exclusively to our API.

## Inputs

- HTTP requests (ticker, assumption overrides)
- Module outputs: `FinancialHistory`, `MarketInputs`, `ModelResult`, workbook bytes
- `methodology.yaml`

## Outputs — API (as built, phase 4 part 1; owner status rules 2026-08-14)

A mechanical translation of the CLI: derive → preset → overrides → `build_model`,
no valuation logic in this layer. `backend/app/` = `main.py` (routes, DI),
`serialize.py` (the JSON contract), `state.py` (the company cache).

| Endpoint | Returns |
|---|---|
| `GET /api/health` | Liveness |
| `GET /api/presets` | Every preset with name/title/rationale/applicability and per-field form + rule (from presets.yaml) |
| `GET /api/methodology` | methodology.yaml conventions + the presets — the single source for the /methodology page |
| `POST /api/model/{ticker}` | Body `{preset?, overrides?, code?, valuation_date?}` → the full model contract below. Explicit fields win over the code (CLI rule) |
| `GET /api/model/{ticker}?code=&preset=&valuation_date=` | Same, for share links |
| `GET /api/reverse/{ticker}?valuation_date=` | The four reverse-DCF solves `{derived, implied, status, target_price}` — no-solution states named, never numbered |
| `GET /api/workbook/{ticker}.xlsx?code=&preset=&valuation_date=` | The formula workbook from the SAME ModelResult the model endpoint serializes (screen == download, contract-tested via LibreOffice recalc). Refused filers → 409 |
| `POST /api/code` / `GET /api/code/{code}` | Compact assumption-set code, both directions |

**Model contract** (`status: "ok"`): `company` (meta + cost_structure +
`filing_basis` from FY0 provenance) · `market` (price/rf/beta with staleness) ·
`preset` (name/title/rationale) · `assumptions`: per field `{name, label, value,
unit, provenance: derived|preset:<name>|user, derived_default, rule, editable}` ·
`provenance_counts` · `valuation.gordon / .exit_multiple`: `{available: true,
value_per_share, vs_price, enterprise_value, equity_value, tv_*, tv_share_of_ev,
bridge[]}` **or** `{available: false, reason: {code, message, detail}}` ·
`wacc` (full build-up) · `ufcf` · `projections` · `crosschecks` · `sensitivity`
(grids, null cells for g ≥ WACC) · `checks` (P1–P8) · `warnings` **structured**
`[{origin, code, message, fiscal_year, item, detail}]` — every inherited stream,
never concatenated · `coverage` · `history` (per-year statements with per-fact
source + restated flags) · `reverse` (included so the assumed-vs-implied
comparison needs no second request) · `code` (canonical for the current set).
All derived display math (vs-price deltas, TV share of EV) is computed
server-side — the browser divides nothing.

**Status discipline (owner rule):** refusals and unavailable states are 200s
with machine-readable reasons — `status: "refused"` (insufficient_coverage,
validation_failed, missing_required_item, insufficient_history,
unsupported_currency), `status: "unsupported"` (financial_company,
known_unsupported), `status: "preset_unavailable"`, and per-leg
`available: false`. HTTP errors are actual failures only: 404 unknown ticker ·
400 malformed code / unknown preset / out-of-domain override (with the
constraint text) · 503 upstream unreachable with nothing to fall back to ·
409 workbook requested for a refused filer. Third-party outages never 500 —
the degradation ladder absorbs them.

**Caching (owner rule):** `CompanyStore` holds assembled history + market inputs
per (ticker, valuation_date), TTL 1h, LRU-capped — assumption edits recompute
against the cached inputs and **never refetch upstream** (contract-tested with a
counting source). The reverse solves are cached alongside (they solve against
derived defaults, so user edits never invalidate them). Refusal verdicts are
cached like successes; transient failures are not, so recovery is immediate.
The SqliteCache under the EDGAR/market clients persists across restarts.

Ops: CORS pinned to `FRONTEND_ORIGIN` · API keys server-side env vars only,
never in responses or errors · overrides validated server-side against the
engine's domain table.

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
