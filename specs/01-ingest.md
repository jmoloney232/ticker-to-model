# Spec 01 — Ingest

Turns a ticker into a clean, validated `FinancialHistory`. Owns everything EDGAR: CIK
resolution, company metadata, the `companyfacts` fetch, tag mapping, period selection,
restatement resolution, and the validation gate. Knows nothing about valuation.

## Inputs

- `ticker: str` (case-insensitive; leading/trailing whitespace stripped)
- `backend/ingest/schema.yaml` — canonical items + tag chains (spec 02)
- Env: `EDGAR_USER_AGENT` (required at startup; refuse to boot without it)
- SQLite cache handle (degradation tiers per spec 00)

## Outputs

`FinancialHistory`:

- `company`: CIK, name, ticker, SIC code + description, fiscal-year-end anchor,
  currency (v1: USD reporters only)
- `periods`: ordered list of fiscal years (target 5, minimum 3), each with:
  - `income`, `balance`, `cashflow`: canonical item → value
  - `period_meta`: start/end dates, duration in days, `is_53_week: bool`
- `provenance`: per (period, item): tag used, accession, filed date, form type,
  `was_restated: bool`, `restatement_delta_pct` when applicable
- `warnings`: structured list (unmapped optional items, restatements >1%, 53-week years,
  cross-check mismatches, `coverage_low`, `immaterial_cash_residual`) — flows through
  engine to UI and workbook
- `validation`: the spec 07 report (must be status=pass or pass-with-warnings),
  including the PL plausibility warnings
- `cost_structure`: `by_function` (COGS/gross profit exist) or `by_nature` (no COGS
  concept — VZ, DAL, MCD, SBUX, DIS; verified by bulk scan). **Owner decision:** an
  explicit field, never an implicit absence — the engine's margin defaults, the Excel
  income-statement block, and the UI all branch on it. COGS and gross profit are
  *absent* for by_nature filers (omit, never zero: gross profit must not collapse to
  revenue). A window with COGS in only some years classifies by_nature. **Phase 2
  must handle both shapes** (gross-margin defaults swap to operating-margin defaults).
- `coverage`: how much of the filing landed in **named** line items vs. residual
  buckets, computed on the latest fiscal year — `assets_named_share`,
  `liabilities_named_share`, `expenses_named_share`, `revenue_named_share`
  (balance-sheet share = 1 − |residual `other_*` buckets we had to derive| / total;
  a filer's own tagged "other" line counts as mapped), plus the top unmapped us-gaap
  tags by absolute magnitude. **`expenses_named_share` additionally counts the
  margin-identity gap** — |revenue − EBIT − Σ named cost lines| — because a
  real-but-tiny tagged `other_operating` line blocks the residual deriver and,
  counted alone, masked MCD's ~$11.5B untagged cost block behind an "E100%"
  reading (owner-approved fix, 2026-08-14; regression-tested against the MCD
  fixture, which now reads E21%). Surfaced in the web app as "N% of reported line items
  mapped". **A falling coverage number is the signal that a filer uses tags the
  schema doesn't know about** — it is the early-warning metric for chain gaps.
  Enforced by the coverage gate (step 7): refuse below 60%, hard warning below 85%

## Pipeline

### 0. Known-unsupported gate

`ingest/known_unsupported.yaml` (data file, never hardcoded) lists filers whose
conventions we cannot honestly support yet, with the reason shown verbatim to the
user (`KnownUnsupportedError`) instead of a generic failure. Current: XOM (annual
income statement under custom extension tags), NEE (capex under custom extension
tags — a regulated-utility presentation). Extension-taxonomy support is a later
phase; diagnoses in `docs/known-limitations.md`.

### 1. Ticker → CIK

`https://www.sec.gov/files/company_tickers.json`, cached 24h in SQLite. Unknown ticker →
`UnknownTickerError` (fuzzy "did you mean" is out of scope).

### 2. Company metadata and rejection

`https://data.sec.gov/submissions/CIK##########.json` → name, SIC, fiscal year end.

**Reject financial companies** (out of scope — their statements are structurally
different) with `FinancialCompanyError` naming the detected category:

| SIC range | Category |
|---|---|
| 6020–6199 | Banks, thrifts, credit institutions |
| 6300–6499 | Insurance carriers and agents |
| 6722, 6726 | Investment offices / funds |
| 6798 | REITs |

The message must be clear and user-facing ("JPM is a bank; bank financial statements
have a fundamentally different structure and are not supported"), never a crash.

### 3. Fetch companyfacts

`https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`.

- `User-Agent: $EDGAR_USER_AGENT` on **every** EDGAR request.
- Global token-bucket rate limit: max 8 req/s (headroom under SEC's 10).
- Retry 429/5xx with exponential backoff (3 tries), then fall to cache tier.
- Raw JSON stored in SQLite cache keyed by CIK with fetch timestamp.

### 4. Fact selection policy

For each tag present, `companyfacts` lists facts with `{start?, end, val, accn, fy, fp,
form, filed, frame?}` per unit. Selection:

- **Units:** `USD` for monetary items, `shares` for share counts, `USD/shares` for EPS.
  Non-USD reporters → `UnsupportedCurrencyError` (v1).
- **Forms:** `10-K`, `10-K/A` only. (10-Q comparatives excluded; annual scope.)
- **Annual durations** (IS/CF): period length 340–380 days. Lengths ≥371 days mark the
  fiscal year `is_53_week = true` (annotated, not normalized — a 53rd week inflates
  apparent growth ~1.9%; the warning keeps growth comparisons honest).
- **Instants** (BS): `end` within ±7 days of the fiscal-year-end anchor for that year
  (tolerance covers 52/53-week drift around the anchor).
- **Fiscal year identity:** a period belongs to the fiscal year whose expected FYE its
  `end` date is nearest. FYE anchor comes from `submissions` (`fiscalYearEnd`) and is
  cross-checked against the observed cluster of `end` dates; observed data wins on
  conflict (log it).
- **Restatement policy — latest filed wins:** the same (tag, period) appears under
  multiple accessions (every comparative year re-appears in the next 10-K; true
  restatements change the value). Select the fact with the max `filed` date. When the
  selected value differs from the earliest-filed value by >1% (absolute relative
  difference), set `was_restated`, record the delta, and emit a warning. This gives an
  as-restated history — the correct basis for forecasting — while making restatements
  (KHC) visible instead of silent.

### 5. Tag mapping

For each canonical item in `schema.yaml`, walk its tag chain in order; first tag that
yields a fact for the period wins. Record which tag won (provenance).

- **Required item, no tag matches** → `MissingRequiredItemError` (model refuses to
  build). Never default to zero.
- **Optional item, no tag matches** → apply the item's documented `missing_rule` from
  the schema (e.g. `treat_as_zero_logged`, `derive`, `omit`) and log an
  `unmapped_item` warning with the tags tried.
- **Composite items** (schema `derive: sum(...)` etc.): compute from components per the
  schema expression; component provenance is retained. Required items may resolve via
  documented composites (d_and_a: MSFT's split Depreciation + Amortization tags;
  pretax_income: MCD's Domestic + Foreign split; shares_basic_wa: net income ÷ basic
  EPS for dual-class filers whose share tags are dimensional — GOOGL, META — always
  with a `share_count_derived` warning, since per-share value is the headline output
  and its provenance must be visible).
- **Derived EBIT** (owner decision): when `OperatingIncomeLoss` is not filed (JNJ,
  some years), EBIT = pretax + interest expense − interest income. This sweeps every
  other non-operating item into EBIT; the `ebit_derived` warning propagates to the
  assembled output and carries the absorbed magnitude (probed from the filer's
  non-operating tags) so the size of the approximation is visible, not just its
  existence.
- **Unsplit investments** (owner decision): filers that tag securities only as a
  combined current+noncurrent total (NVDA FY2026) get `investments_combined_unsplit`,
  mapped only when the split items are absent, **excluded from net debt by default**
  with an `unsplit_investments` disclosure — merged securities treated as current
  cash would overstate net cash and understate EV. User-overridable later.
- **Split adjustments** (owner decision): share/EPS-unit facts restated by later
  filings are split recasts, labeled `split_adjustment` — not restatements, excluded
  from H6.
- **Cross-checks** (schema `cross_check`): where both a reported aggregate and a derived
  value exist (e.g. `GrossProfit` vs `revenue − cost_of_revenue`), compare and emit a
  warning above tolerance (spec 07 owns tolerances).
- **Custom extension tags** (company namespace) are not consumed in v1; their existence
  under a mapped concept's presentation is not detectable from `companyfacts` alone, so
  the guard is the cross-check + validation gate. Log-only support is a stretch goal.

### 6. Period assembly

A fiscal year is usable when it has an annual IS/CF duration and a matching BS instant
(proxy trio: revenue, operating cash flow, total assets). Take the **most recent
gapless run** of usable years, up to 5 — never interpolate across a gap. If a gap
truncates the run below the 5 requested, build with the shorter run and emit a
`history_trimmed_at_gap` warning naming the gap year; if the run is shorter than 3,
`InsufficientHistoryError` naming the gap.

The same policy applies to **required items** (owner decision): a required item
missing only in the *oldest* years trims the window (`history_trimmed_required`
warning, 3-year floor); missing in a recent year still hard-errors — we keep the most
recent run in which every required item resolves.

### 7. Coverage gate (owner-approved 2026-08-13)

A filer that builds badly is more dangerous than one that fails, because nothing
signals the user to distrust it. Gate on `min(assets_named_share,
liabilities_named_share)`:

- **< 60%** → `InsufficientCoverageError`: no valuation. The message is diagnostic —
  it names the largest unattributed balances (the derived residual buckets, then the
  biggest unmapped balance-sheet tags) with magnitudes. Verified: DE (20%/18% — a
  captive-finance balance sheet; see `docs/known-limitations.md`).
- **60–85%** → builds behind a `coverage_low` warning stating both shares and the
  largest unattributed balances. **UI contract (spec 06): rendered as a hard,
  non-dismissible banner.** Verified: NVDA (73% assets, dominated by the disclosed
  unsplit-investments item).
- **≥ 85%** → clean.

Floors calibrated on the 29-ticker scan, where the distribution is bimodal (DE at
20%, then nothing until 73%): 60% separates most-of-the-balance-sheet disasters from
real but bounded gaps. Constants live in `ingest/assemble.py`; mirrored in
methodology.yaml.

### 8. Validation gate

Run spec 07 tie-outs on the assembled history. `fail`-severity → `ValidationError`
carried to the user with the failing identity, magnitudes, and per-item provenance —
loud, specific, never a silently-untied model. H2's immaterial-residual band (spec
07) additionally surfaces an `immaterial_cash_residual` warning in the assembled
output quantifying each affected year in dollars and percentages.

## Invariants

- Every emitted number has provenance (tag, accession, filed date).
- No value is ever silently zero: zeros are either reported facts or documented
  `missing_rule` outcomes with a warning attached.
- Periods are strictly ordered, no duplicates, no gaps.
- Balance sheet facts are instants; IS/CF facts are durations (mixing shapes is a bug).
- Output is deterministic for a fixed raw `companyfacts` payload (pure given its
  inputs + schema version).

## Error cases

| Error | Trigger | User-facing behavior |
|---|---|---|
| `KnownUnsupportedError` | Ticker in known_unsupported.yaml | The listed reason, verbatim |
| `UnknownTickerError` | Not in company_tickers.json | "Ticker not found" |
| `FinancialCompanyError` | SIC in rejection table | Category-specific rejection message |
| `UnsupportedCurrencyError` | No USD facts | Clear unsupported message |
| `InsufficientHistoryError` | <3 usable fiscal years, or a gap | Explains what was found |
| `MissingRequiredItemError` | Required item unmappable | Names item + tags tried |
| `InsufficientCoverageError` | min(assets, liabilities) named-share < 60% | Names the largest unattributed balances + magnitudes |
| `ValidationError` | Spec 07 fail | Shows failing tie-out + values |
| `EdgarUnavailableError` | Network/5xx after retries **and** no cache/snapshot | Graceful "try later" state (the one allowed dead end, spec 00) |

## How tested

- **Fixtures:** committed raw `companyfacts.json` + `submissions.json` for MSFT (clean,
  June FYE — happy path + non-calendar fiscal year), KO (clean, Dec FYE — hand-checkable
  values), COST (52/53-week detection; FY2023 = 53 weeks), KHC (restated FY2016–17 →
  exercises latest-wins + >1% warning), JPM (SIC 6021 → rejection path). Captured by a
  snapshot script; refreshed deliberately.
- **Unit tests per policy:** fact selection (form filter, duration windows, instant
  matching), restatement resolution (synthetic multi-accession fixtures with known
  deltas), fiscal-year assignment across FYE drift, tag-chain fallback order, required
  vs. optional missing behavior, every error in the table above.
- **Golden outputs:** assembled `FinancialHistory` for each fixture serialized and
  snapshot-tested; a value-level spot check against the actual filed 10-K numbers for
  MSFT and KO (hand-verified once, then frozen).
- **Live smoke test** (marked, excluded from CI): full pipeline against live EDGAR for
  MSFT to catch endpoint drift.
