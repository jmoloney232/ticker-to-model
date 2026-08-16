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
`provenance_counts` · `valuation`: an **ordered list** of methods from the
engine's registry (`gordon`, `exit_multiple`, `epv` — order server-owned), each
`{id, label, note, available: true, value_per_share, vs_price,
enterprise_value, equity_value, detail: [{key, label, unit, value}], bridge[]}`
**or** `{id, label, note, available: false, reason: {code, message, detail}}` —
the UI iterates and never names legs in rendering code ·
`growth`: `{available, state: positive|value_destructive|unavailable, per_share,
share_of_dcf, text, epv_text}` — the server-written value-of-growth sentence,
phrased once per view (owner spec 2026-08-16) ·
`families`: `[{id: dcf|epv, label, blurb, fields}]` — the view selector's
content, server-owned; `fields` is the EPV view's exact assumption surface
(perturbation-tested against the engine), `null` = full surface. The user picks
the view on the landing page or the in-page switcher; `?view=epv` carries it in
links, absent = DCF. The EPV view renders only its family's methods, its own
`epv_verdict` (states ok | negative_equity | no_epv), and the filtered Model
tab; DCF machinery (slider, drivers, grids, projections, presets) stays in the
DCF view ·
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

## Outputs — frontend (as built, phase 4 part 2 — owner picked direction 1c, 2026-08-14)

The mockup (`docs/design/valuation-tool-directions.dc.html`, board **1c
"Bridge"**) is the specification; tokens extracted per
`docs/design/tokens-proposal.md` into `frontend/src/tokens.css`. Two approved
DS extensions: **IBM Plex Mono** as `--font-mono` (self-hosted woff2 committed
under `frontend/public/fonts/` — no runtime font CDN call) and the semantic
tones `--warn` / `--down` / `--down-on-dark`. An adherence lint
(`scripts/adherence.mjs`, `npm run lint:ds`) forbids raw colors and fonts
outside tokens.css; the mockup's structural constants (board width, chart
geometry, grid column specs) stay component-level, annotated `ds:`.
Hand-rolled history routing, zero runtime dependencies beyond react/react-dom.
Desktop tool at the mockup's fixed 1440px board — no mobile layout (owner).

Routes:

- `/` — ticker entry; fixture examples; one-paragraph pitch.
- `/company/:ticker` — the 1c board:
  - **Header strip:** ticker input (navigates on Enter) · company name · price +
    as-of + staleness chip · filing basis (FY/form/filed) · WACC + β · the four
    presets from `GET /api/presets` as always-visible tiles (title + rationale,
    ● active / ○ inactive); "derived defaults" tile = no preset.
  - **Hero (reversed steel field):** assumed vs market-implied perpetual growth
    at 52px with the gap in pp; no-solution solves stated in words. Straddle
    chart: Gordon and exit bars (hatched) around the market-price bar, deltas
    from the API's `vs_price` (`--down-on-dark` when below); an unavailable leg
    renders as a dashed reasoned plate. Caption sentence composed from API
    fields only.
  - **Assumptions:** two balanced columns, presentation-only grouping (unknown
    fields land in "Other", never dropped). Per row: label · value input in
    display units (edits parsed back to engine-native) · provenance glyph + source
    (■ derived / □ preset / ● you / ƒ computed) · per-field reset on overrides;
    global reset in the pane band. The hovered row's rule prints in a fixed
    **inspector strip** at the pane's foot (1a's device, adopted); overridden
    rows also show their derived default there. Out-of-domain override → 400
    constraint text shown, state reverted to last good.
  - **Sensitivity pane:** both heat grids (WACC × g 5×9, WACC × multiple 5×5),
    mockup heat ramp, base cell ringed, null cells "—", unavailable grids as
    reasoned text. Stat block: cells reaching price · TV share of EV both legs ·
    fields at derived default · coverage shares. Market-implied solves table
    (all four, no-solution states in words).
  - **Caveats:** every warning from every origin, structured; a code repeating
    4+ times folds into a disclosure (all rows still rendered — nothing
    droppable); `coverage_low` never folds and is marked in `--down`. Any failed
    check → loud rust band directly under the hero.
  - **Download workbook** → `GET /api/workbook/{ticker}.xlsx?code=` with the
    model response's canonical code — screen == download by construction.
- `/methodology` — conventions + presets from `GET /api/methodology`, grouped by
  category; presets render automatically from `engine/presets.yaml` (owner
  contract, 2026-08-14).

State machine per the status discipline: `ok` → board · `refused` /
`unsupported` → full-width reasoned card (first-class content, registration
marks, machine detail block) · `preset_unavailable` → card + "return to derived
defaults" · 404 → unknown-ticker card · 503/network → retry card ("nothing
cached yet — try again") · loading → quiet mono note; recompute-in-flight → a
thin steel busy hairline under the header (static under
`prefers-reduced-motion`). The share code lives in the URL (`?c=`), written via
`replaceState` after each ok response and decoded through `GET /api/code/{code}`
on load; malformed codes are dropped and the derived model builds.

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

- **API contract tests** (`tests/test_api.py`, 21) against a fixture-backed app
  (no live third-party calls): every endpoint and state, engine-parity to
  1e-12, screen == downloaded workbook via LibreOffice recalc, edits never
  refetch upstream (counting source), override validation, code round-trip.
- **Frontend** (`npm test`, Vitest + Testing Library, 20): assumptions panel
  default/override/reset/unit-parsing/null-value/unknown-field logic; contract
  states (unavailable legs, folded + hard caveats, failed checks, refusal and
  preset-unavailable cards, no-solution solves); page-level fetch loop against
  a stubbed API asserting values render verbatim and the workbook link carries
  the same code. `npm run build` typechecks; `npm run lint:ds` enforces token
  adherence. A browser E2E (Playwright) is deliberately absent — its browser
  download is a new outside-project dependency needing owner sign-off.
