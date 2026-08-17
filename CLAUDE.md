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
| 1 | `backend/ingest/` — EDGAR fetch, tag mapping, periods, validation | **done — 130 tests, 9 real-filing fixtures; final scan: 23/27 non-financials build, DE coverage-refused, GE spin-year isolated, XOM+NEE honestly rejected; limitations in docs/known-limitations.md** |
| 2 | `backend/engine/` + `backend/market/` — projections, WACC, DCF | done (owner-reviewed) — incl. reverse DCF, diagnostic fixes (margin-identity closure, negative-anchor guards), assumption presets with provenance |
| 3 | `backend/excel/` — formula-driven workbook | **built — 7 sheets, engine-field-named ranges, live checks, semantic guards in formulas; gated by LibreOffice round-trip (MSFT/MCD/GOOGL cell-diff vs engine, KHC unavailable-states) + liveness tests; awaiting owner review** |
| 4 | `backend/app/` + `frontend/` — API, dashboard, download | **API done (owner-reviewed); frontend built — direction 1c, all states, 20 tests; g-grid widened to 5×9 @25bp (owner decision); awaiting owner review** |

Dev: `cd backend && .venv/bin/python -m pytest` (venv via `python3 -m venv .venv` +
`pip install httpx pyyaml pytest ruff openpyxl fastapi uvicorn`). The Excel
round-trip gate needs LibreOffice (`brew install --cask libreoffice`); without it
those tests skip loudly and the phase 3 gate has NOT run. Fixture refresh:
`EDGAR_USER_AGENT=... python -m ingest.snapshot MSFT KO COST KHC JPM`.
Frontend: `cd frontend && npm install && npm run dev` (proxies `/api` to
`127.0.0.1:8000`); `npm test` (Vitest), `npm run build` (typecheck + bundle),
`npm run lint:ds` (token adherence — raw colors/fonts outside `tokens.css` fail).

## Decision log (recent additions)

- **Terminal beta convergence (owner, 2026-08-17):** β fades linearly from the
  Blume-adjusted current estimate to `terminal_beta` = midpoint(β, 1.0) by the
  final explicit year; explicit flows discount along the per-year WACC path
  (cumulative df row, live in the workbook); every perpetuity — Gordon TV and
  EPV's stable phase (two-phase EPV) — capitalizes at the terminal WACC; the
  g ≥ WACC block, the spread floor, and the implied-g crosscheck bind on the
  terminal rate; grids/drivers shift the whole path in parallel. Weights, Kd,
  rf, ERP held. Reduction invariant tested: terminal_beta = β reproduces the
  single-WACC model. Methodology: `terminal_beta_convergence`.
- **Terminal spread floor (owner, 2026-08-17, implemented):** derived g
  clamped at terminal WACC − 2% (no-op today); `terminal_spread_thin` warns
  on user/preset values in the band, never clamps. The motivating rate-swing
  concern was tested and DISPROVED — recorded in methodology.
- **Classifier capex basis (owner, 2026-08-17):** capex/depreciation replaces
  capex/D&A in the reinvestment-heavy measure and the fade-mismatch trigger
  (thresholds untouched). Flips: DIS/MRK/NOW gain the modifier; LLY (4.25×)
  and ORCL (4.99×) cross the 4× cap and lose it (ORCL −24.5%); CSCO discloses
  at 16×. Pinned in fixture regressions.

- **Split D&A basis (owner, 2026-08-16):** PP&E roll consumes depreciation
  only (D&A − intangible amortization, subtraction-derived, identity floor
  dep ≤ beg PP&E + capex); intangibles run off at their own rate, add-back
  expires with the balance (run-off treatment chosen over no-add-back /
  perpetual); combined basis retained + `amortization_unobservable` when the
  filer doesn't tag the split. Fixes AVGO (−$342.69 → $91.36). Classifier
  capex/D&A basis deliberately deferred (known-limitations §13). D + ED are
  known_unsupported with corrected verified reasons (NOT NEE-class: D nets
  disposals into its only capex tag; ED files PaymentsForConstructionInProcess
  — both one-line chain adds if utilities enter scope).
- **Half-cosine fade removed (owner-delegated, 2026-08-16):** worth ≤$2/share
  across the whole compounder cohort — deleted, not documented; linear fade
  only; compounder profile = exactly two levers (10y horizon, g at 10Y).
- **Terminal spread floor (proposal only, 2026-08-16):**
  `docs/proposals/terminal-spread-floor.md` — measured: no filer below a 2.5%
  spread (g ≤ rf + one-WACC bounds spread ≥ ~wE·β·ERP); rf ±100bp moves values
  only ±3–5% (g and WACC co-move); real hole is user overrides. Awaiting owner.
- **Structural-bias re-measurement (2026-08-16):** `python -m diagnostics
  --bias` runs profiles-off vs profiles-on arms. Result on the 47-name
  universe: growth decorrelated (−0.25 → +0.10), WACC partial (−0.61 → −0.49),
  beta unchanged (−0.40 → −0.39); INSIDE the compounder cohort WACC/beta
  correlations worsened (−0.50 → −0.57, −0.45 → −0.52) — the g-at-rf lever's
  1/(WACC−g) payoff is itself WACC-shaped. Reported as measured; no tuning.

- **Sensitivity g-axis (owner, 2026-08-14):** WACC × g grid is 5×9 — g ±100 bp
  at 25 bp steps (was 5×5 at 50 bp), symmetric, base center; convention in
  methodology.yaml. Old cells survive verbatim as the new even columns.
- **Frontend direction (owner, 2026-08-14):** mockup board 1c "Bridge"; DS
  extensions approved: IBM Plex Mono (self-hosted, committed woff2) + --warn /
  --down / --down-on-dark. Frontend deps stay react/react-dom only; no
  Playwright without explicit sign-off (outside-project browser download).
- **EPV + methods registry (owner, 2026-08-15):** valuation output is a
  registry of independent methods (gordon, exit_multiple, epv — ordered,
  availability-honest; reverse DCF is NOT a method); API/workbook/frontend
  iterate it, adding a method touches no serializer/frame/rendering code.
  EPV = FY0 revenue × epv_margin × (1−marginal) ÷ WACC with DCF timing;
  epv_margin per profile (mature/compounder 3y mean, cyclical full window,
  declining latest year; declining wins collisions); maintenance capex = D&A
  (D&A cancels). Value of growth = Gordon − EPV (Gordon comparator only);
  EPV > DCF is a labeled value-destructive state, never a negative number.
  Property-tested: g=0 DCF converges to EPV at rel 1e-9.
- **DCF/EPV view split (owner, 2026-08-16):** the user selects a view at the
  start (landing buttons; in-page switcher; `?view=epv` in links, absent =
  DCF). Views are server-owned method families; the EPV view exposes ONLY
  the assumptions EPV consumes (`EPV_FIELDS` in engine/dcf.py — perturbation-
  tested exact), its own server-written verdict, and the growth line phrased
  per view. DCF machinery (slider, drivers, grids, projections, presets)
  never renders in the EPV view. The workbook is never split.

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
- **Cost structure (owner decision):** COGS is optional; by-nature filers (VZ, DAL,
  MCD…) classify `cost_structure: by_nature` — an explicit field downstream code
  branches on. Phase 2 must handle both shapes (gross vs. operating margin defaults).
- **Derived EBIT (owner decision):** pretax + interest when the subtotal isn't filed;
  `ebit_derived` warning propagates with the absorbed non-operating magnitude.
- **H2 outcomes are three-way:** clean tie / definitional cash mismatch (warn,
  restricted-cash & disposal-group variants) / real break (fail).
- **Dual-class shares:** WA counts derived as NI÷EPS when share tags are dimensional
  (GOOGL, META); always warned (`share_count_derived`) — per-share provenance visible.
- **Unsplit investments (owner decision):** combined current+noncurrent securities
  totals (NVDA) map to `investments_combined_unsplit`, excluded from net debt by
  default with disclosure.
- **Split adjustments:** share/EPS-unit recasts are labeled splits, not restatements.
- **Known-unsupported list:** `ingest/known_unsupported.yaml` (XOM, NEE —
  extension-tag filers) returns an honest message, never a generic error.
- **H2 materiality band (owner decision):** an unreconciled cash residual below
  1% of revenue AND 5% of gross flows builds with a distinct per-year quantified
  `immaterial` outcome + `immaterial_cash_residual` warning; above either leg it
  fails. Applied to both the Δ-cash and reported-net-change residuals. Verified:
  AMZN/TSLA/F/DIS build; GE FY2022 (spin year, 1.27% of revenue) still fails.
- **Coverage gate (owner decision):** min(assets, liabilities) named-share < 60%
  refuses with the largest unattributed balances named (DE); 60–85% builds behind
  a hard, non-dismissible `coverage_low` warning (NVDA).
- **Known-limitations doc:** `docs/known-limitations.md` — what breaks, why, and
  what fixing it would require (GE spin year, extension-tag filers, MCD lessee
  leases, captive finance, linear-fade cumulative path, flat leases). Deliberate
  asset, kept current.
- **D&A placement (owner decision):** cost lines projected D&A-inclusive as filed
  (EBIT = revenue − cost lines, no separate subtraction); roll D&A is a memo line
  for CF/BS/EBITDA only — separate-line projection would double-count D&A.
- **Growth default cap (owner decision):** FY1 revenue growth = 3y CAGR capped at
  30%, uncapped CAGR displayed alongside; soft warning above 25%.
- **Unobservable interest (owner decision):** zero-logged interest expense with
  material debt is imputed at synthetic Kd (warned); zero-logged interest income
  stays 0 (warned) — omit unobservable income, impute unobservable expense.
- **Beta re-confirmed (owner, 2026-08-13):** 2y weekly vs SPY, Blume-adjusted —
  Bloomberg's default, citable; chosen over 3y daily (non-synchronous-trading bias).
- **Held-flat noncurrent lines (owner decision):** every BS line has exactly one
  documented projection rule; nothing reaches the cash plug silently.

## Scope guardrails (v1)

Large US non-financial companies, annual periods, 5-year default forecast
(selectable 5/7/10 — owner decision, audit round 2026-08-14). Banks, insurers,
REITs: detect by SIC code and reject with a clear message — never produce garbage. Out
of scope (architect for, don't build): segments, footnotes, MD&A, quarterly data, comps,
LBO. Fixture tickers: MSFT (clean, June FYE), KO (clean, calendar FYE), COST (52/53-week
retailer), KHC (restatement), JPM (bank → rejection).
