# Spec 04 — Valuation engine

Pure functions: `(FinancialHistory, MarketInputs, Assumptions) → ModelResult`. No I/O,
no framework imports. The engine is the single source of truth for every number; the
Excel writer re-expresses this math and is parity-tested against it (spec 05). Every
convention referenced here is defined in `backend/engine/methodology.yaml` — the two
must agree item-for-item (checked in CI once code exists).

## Inputs

- `FinancialHistory` (spec 01) — validated, ≥3 fiscal years
- `MarketInputs` (spec 03) — price, beta (raw + adjusted), risk-free rate
- `Assumptions` — every field carries `{default, derivation, override?}`; effective
  value = override if present else default. Defaults are computed by
  `derive_assumptions(history, market)` (also pure).

## Assumption defaults and derivation rules (owner-reviewed 2026-08-13)

All derived from the company's own history; all editable; all shown with their
derivation text in the UI and workbook. Notation: FY0 = latest historical year,
n = periods available (3–5); "3y mean" = arithmetic mean of the *per-year* ratio
over the last min(3, n) fiscal years (per-year then averaged, so a mix-shift year
stays visible). 53-week years enter averages unadjusted, consistent with ingest's
annotate-don't-normalize policy (H7 flags them).

**Cost-structure branching (ingest contract):** `by_function` filers carry
COGS/gross profit; `by_nature` filers (VZ, DAL, MCD, SBUX, DIS) have *no COGS
concept* — the cost block lives in `other_operating`, so the effective driver is
the operating margin. Both paths are first-class and both are tested.

**D&A placement (owner decision):** filed cost lines *contain* D&A (filers embed
depreciation in COGS and SG&A; ingest cannot and does not split it out). Cost
ratios are therefore measured and projected **D&A-inclusive as filed**, and EBIT =
revenue − Σ cost lines with **no separate D&A subtraction**. Roll-forward D&A is a
**memo line** used for exactly three things: the CF add-back, the PP&E roll, and
EBITDA = EBIT + memo D&A. Every identity holds exactly (P1/P2); the disclosed
nuance is that embedded-D&A-in-ratios and memo D&A can drift apart in later
projection years (methodology surface carries this).

| Assumption | Default derivation | Fallback when history is short/missing |
|---|---|---|
| Revenue growth FY1 | CAGR `(rev_FY0/rev_FY0−k)^(1/k) − 1`, k = min(3, n−1), **capped at 30% (owner decision)** — the uncapped CAGR is displayed alongside the capped default, clearly labeled, and the user can override upward; soft warning `growth_fade_steep` above 25% | n=3 → 2y CAGR |
| Revenue growth FY2–FY5 | linear fade `g_i = g_1 + (i−1)/4·(g_term − g_1)`; FY5 growth = terminal g exactly | — (curved fade is a documented v1.1 candidate; see known-limitations) |
| Gross margin *(by_function only)* | 3y mean gross_profit/revenue (D&A-inclusive as filed) | by_function guarantees COGS in-window (mixed windows classify by_nature) |
| R&D / SG&A / other operating, each % of revenue | 3y mean of line/revenue for lines present; by_nature filers project whichever lines exist — `other_operating` carries the cost block | zero_logged line → 0% with the inherited warning surfaced |
| D&A (memo) | rate = 3y mean of `d_and_a_t / ppe_net_{t−1}` (beginning balance) | PP&E unmapped → 3y mean % of revenue (disclosed) |
| Capex % of revenue | 3y mean | required item — always present |
| SBC % of revenue | 3y mean | zero_logged → 0% + warning (add-back toggle becomes a no-op; disclosed) |
| DSO | 3y mean `365·AR_t/revenue_t` | AR zero throughout → 0 |
| DIO / DPO | by_function: `365·inv_t/COGS_t`, `365·AP_t/COGS_t`. **by_nature: denominator = revenue − operating income** (total operating costs). Safe because the same denominator drives both the historical ratio and the projection — any distortion cancels inside the model. **It is a projection ratio, never presented as a comparable DIO/DPO figure** | inventory absent → DIO 0 |
| Other current assets / accrued liabilities / other current liabilities / deferred revenue (current), each % of revenue | 3y mean | absent → 0 with inherited warning |
| Effective tax rate (FY1) | 3y mean of income_tax/pretax **excluding years with pretax ≤ 0**, clamped [10%, 35%] | all in-window years loss-making → marginal from FY1 |
| Marginal tax rate | 25% flat (21% federal + state blend); terminal NOPAT + after-tax Kd rate | — |
| Dividend payout ratio | 3y mean dividends/NI over **years with NI > 0**, clamped [0, 1] | no positive-NI years or dividends zero_logged → 0 |
| Interest expense (P&L) | embedded rate = 3y mean `interest_expense_t / gross_debt_{t−1}` × beginning gross debt | zero_logged with material debt → **imputed at synthetic Kd** + `interest_imputed` warning (see interest asymmetry, methodology) |
| Interest income yield | 3y mean `interest_income_t / (cash+STI)_{t−1}`, clamped [0, rf] | zero_logged → 0% + warning (asymmetry is deliberate: omit unobservable income, impute unobservable expense — both conservative) |
| Other non-operating | **projected at 0** (one-offs are not run-rate; historicals still displayed, so the level-break is inspectable). For `ebit_derived` filers it is already inside EBIT ratios — the inherited warning carries the absorbed magnitude | — |
| Beta | Blume-adjusted 2y weekly from spec 03 (toggle: raw; manual override) | <80 obs → β = 1.0 + loud warning |
| Equity risk premium | 5.0% | — |
| Risk-free rate | Spot DGS10 (override for a normalized rate) | cached/snapshot with staleness label |
| Terminal growth g | `max(1.5%, min(2.5%, 10Y yield))` — floor prevents distorted-rate regimes from mechanically crushing valuations | — |
| Terminal ROIC (`ROIC_t`) | 3y mean of `EBIT_t(1−marginal) / IC_{t−1}`; IC = gross debt + stockholders equity + NCI + preferred + temporary equity − cash − STI (beginning balance) | IC ≤ 0 years dropped; none usable or ROIC_t ≤ g → **ROIC_t = WACC** + `roic_fallback` warning stating plainly that terminal reinvestment is value-neutral because returns could not be estimated |
| Exit EV/EBITDA multiple | current EV / FY0 EBITDA; EV = mcap + gross debt − cash − STI (**`investments_combined_unsplit` excluded**, per the ingest owner decision); EBITDA = EBIT + memo-basis D&A | EBITDA ≤ 0 → exit leg marked unavailable (Gordon still runs) |
| Synthetic-Kd coverage | `(Σ 3y EBIT)/(Σ 3y interest_expense)` — ratio of sums, not mean of ratios (a near-zero interest year would explode a per-year ratio) | interest zero_logged → top coverage bracket, disclosed |
| Operating cash floor | 2% of revenue (cash below this is operating, not excess) | — |
| Toggles | Mid-year discounting (default on) · SBC add-back (default off = expensed) · Kd method (default synthetic; embedded toggle falls back to synthetic + warning when interest is zero_logged) · beta adjustment (default Blume) | — |

**Data honesty (ingest contract):** every consumed Fact carries `source`
(tag / derived / zero_logged). A zero that means "unmapped" must never render the
same as a zero that means "genuinely zero" — engine output, CLI, and UI all label
zero_logged inputs and pass the inherited warnings through, including
`coverage_low` and `immaterial_cash_residual` (mandatory pass-throughs), and the
`share_count_derived` / `ebit_derived` provenance warnings.

## Projection mechanics (FY1–FY5)

Three full statements, built so the exported workbook needs **no circular references**:

- **Income statement:** revenue from growth path; cost lines from ratio assumptions,
  **D&A-inclusive as filed — EBIT = revenue − Σ cost lines, no separate D&A
  subtraction** (see D&A placement above; memo D&A = rate × *beginning* net PP&E
  feeds only CF/BS/EBITDA); other non-operating projected at 0; **interest expense =
  embedded historical rate × beginning gross debt** (debt held constant — see
  financing policy; rate imputed at synthetic Kd when interest is zero-logged with
  material debt); **interest income = yield assumption × beginning (cash + ST
  investments)**; taxes at the effective→marginal fade (linear across FY1–FY5,
  hitting marginal in the terminal year). Note the deliberate split: projected P&L
  interest uses the *embedded* rate (cost of the legacy debt actually outstanding);
  the *WACC* uses the marginal rate (synthetic rating) — different questions,
  different rates, both documented.
- **Balance sheet:** AR/inventory/AP from DSO/DIO/DPO; other current operating items
  % of revenue; net PP&E rolls `begin + capex − memo D&A`; debt constant; equity
  rolls `begin + NI − dividends`; **cash is the plug** (no revolver in v1 —
  acceptable for the large-cap non-financial universe; a deeply negative cash plug
  raises a `cash_plug_negative` warning rather than fabricating a revolver).
  **Held-flat lines — a modeling choice with a stated rationale, not an omission
  (owner decision):** goodwill, intangibles, LT investments, operating-lease ROU and
  liability, deferred tax liabilities, pension, other noncurrent assets/liabilities,
  NCI, preferred, temporary equity are all held at FY0. Rationale: none has a
  defensible history-derived growth rule in v1 (acquisitions aren't modeled,
  lease-footprint growth is a v1.1 candidate — see known-limitations), and an
  explicit flat line is inspectable where a silent default is not. **Every
  balance-sheet line has exactly one documented rule; nothing reaches the cash plug
  silently** — the plug absorbs only the residual of stated rules.
- **Financing policy (v1, disclosed):** debt constant, no buybacks, dividends at the
  payout-ratio assumption, share count constant at the current-diluted proxy.
- **Cash flow statement:** derived indirectly from IS + ΔBS; must tie to Δcash exactly
  (invariant P2).

## Unlevered free cash flow

```
UFCF_t = EBIT_t × (1 − tax_t)            # NOPAT
       + D&A_t
       + SBC_t   × [SBC add-back toggle, default OFF — SBC stays a real expense]
       − capex_t
       − ΔNWC_t                          # NWC excludes cash, ST investments, and all debt
```

SBC projected as % of revenue (3y average) so the toggle works in projections; when the
toggle is off it simply is not added back (it is already an expense inside EBIT).

## WACC

- **Cost of equity:** `Ke = rf + β × ERP` (β per toggle: Blume-adjusted default).
- **Cost of debt (default: synthetic rating):** interest coverage `EBIT ÷ interest
  expense` (3y average) → rating → default spread → `Kd = rf + spread`, after-taxed at
  the **marginal** rate (same rate as terminal NOPAT — mismatching these is a classic
  silent bug and is asserted against). Toggle: embedded rate (interest expense ÷ average
  gross debt) — measures the legacy coupon, kept as the non-default option because it
  understates marginal financing cost when rates have risen.
  Spread table (adapted from Damodaran's synthetic-rating tables; **placeholder values,
  verify against the current published table at build time**; stored in
  methodology.yaml):

  | Coverage | Rating | Spread |
  |---|---|---|
  | > 12.5 | AAA/AA | 0.70% |
  | 9.5 – 12.5 | A+ | 0.90% |
  | 7.5 – 9.5 | A | 1.05% |
  | 6.0 – 7.5 | A− | 1.20% |
  | 4.5 – 6.0 | BBB+ | 1.50% |
  | 3.5 – 4.5 | BBB | 1.80% |
  | 3.0 – 3.5 | BB+ | 2.50% |
  | ≤ 3.0 | BB and below | 4.00% |

- **Weights:** `E = market cap` (price × current-diluted share proxy), `D = gross book
  debt` (ST + LT). **Gross debt in WACC weights; net debt appears only in the EV→equity
  bridge** — using net in both is the most common WACC error; this placement is a tested
  invariant (P3). Book debt as market-value proxy is standard for investment-grade
  issuers (disclosed).

## Discounting (valuation date, stub, mid-year)

- `ValuationDate` = today (a stamped input, not a live formula — reproducibility).
- Exponent for FY_i's FCF, **mid-year on** (default): `t_i = (midpoint(FY_i) −
  ValuationDate) / 365.25`; **mid-year off:** `t_i = (FYE_i − ValuationDate) / 365.25`.
  This handles the stub automatically — a valuation run mid-fiscal-year discounts the
  partial first year correctly instead of silently assuming a full year.
- `DF_i = (1 + WACC)^(−t_i)`; `PV(explicit) = Σ UFCF_i · DF_i`.

## Terminal value — two methods, cross-checked

**Gordon growth with reinvestment consistency** (growing year-N FCF at g forever
implicitly assumes infinite marginal ROIC — the classic hidden TV inflator):

```
reinvestment rate RR = g / ROIC_t                     (hard requirement: RR < 1)
NOPAT_{N+1} = EBIT_N × (1 + g) × (1 − marginal tax)
FCF_terminal = NOPAT_{N+1} × (1 − RR)
TV_gordon(at FYE_N) = FCF_terminal / (WACC − g)       (hard requirement: g < WACC)
PV(TV_gordon) = TV_gordon / (1 + WACC)^(t_N − 0.5·midyear)
```

Mid-year on → discount at `t_N − 0.5` (perpetual flows arrive through each year).

**Exit multiple:** `TV_exit = multiple × EBITDA_N`, discounted at **full `t_N`
regardless of the mid-year toggle** — a sale is a point-in-time year-end event. This
asymmetry is deliberate, documented, and unit-tested (mid-year is worth ~2–4% of value;
a bug here is invisible and material).

**Implied cross-checks** (standard practitioner discipline; replaces a comps-less
disclaimer; closed forms shared verbatim by engine and workbook for parity):

```
implied_exit_multiple = TV_gordon / EBITDA_N
implied_g             = WACC − FCF_terminal / TV_exit     (labeled approximation:
                                                           holds the Gordon leg's
                                                           reinvestment fixed)
```

Displayed as: "Your 2.5% terminal growth implies an exit multiple of X.X×" / "Your
12.0× exit multiple implies terminal growth of X.X%".

## EV → equity bridge

```
Enterprise value      = PV(explicit UFCF) + PV(TV)          [per TV method]
+ excess cash         = max(0, cash + ST investments − cash floor)
+ long-term investments (book, labeled non-operating)
− gross debt
− noncontrolling interest (book value; disclosed proxy)
− preferred equity
− unfunded pension liability (after-tax at marginal rate)
= Equity value
÷ current-diluted share proxy  (cover-page basic × latest diluted/basic WA ratio —
                                TSM approximation, footnote option data out of scope)
= Value per share
```

The share proxy consumes ingest's `shares_current` with its provenance chain:
`dei` cover count → undimensioned `us-gaap:CommonStockSharesOutstanding` (GOOGL) →
derived latest-FY WA count (META, always with `share_count_derived`). The dilution
ratio's own components may be NI÷EPS-derived for dual-class filers — the inherited
warning travels with the per-share output.

Missing optional bridge items are logged, never silently zero, and a `zero_logged`
component renders as "0 — unmapped (see warning)", never as a bare zero: pension in
particular is usually unmapped, and "no pension data" must not read as "no pension".
`investments_combined_unsplit` (NVDA-shape) stays out of the bridge and out of
current EV, per the ingest owner decision, with its disclosure warning passed through. Operating leases stay
out of net debt (EBITDA is unadjusted — both sides consistent); when the operating
lease liability exceeds 25% of gross debt, emit the `lease_heavy` warning (retail /
restaurant / airline names — COST exercises this).

## Sensitivity grids

Two 5×5 grids of value per share: **WACC × terminal g** (Gordon) and **WACC × exit
multiple**. Center = base case; steps ±0.5% WACC, ±0.5% g, ±1.0× multiple. Full DCF
re-computed per cell (only the varied inputs change).

## Outputs — `ModelResult`

Projected IS/BS/CF (FY1–FY5) · UFCF schedule with discount factors · WACC components
(each intermediate value exposed: coverage, rating, spread, Ke, Kd, weights) · both TVs
with PVs and % of EV · implied cross-checks · EV→equity bridge (every line) · value per
share (both methods) vs current price · sensitivity grids · assumptions echo
(default/derivation/override per field) · warnings (incl. inherited ingest warnings) ·
validation report (projection invariants).

## Invariants

- P1: projected balance sheet balances **exactly** every year (plug construction, still asserted).
- P2: projected cash flow ties to Δcash **exactly** every year.
- P3: WACC weights use gross debt; net debt only in the bridge (structural + tested).
- P4 (hard): `g < WACC`, `g/ROIC_t < 1`, `WACC > 0`, shares > 0.
- Terminal NOPAT and after-tax Kd use the same marginal rate (asserted).
- Mid-year off + ValuationDate = FYE_0 reproduces textbook year-end DCF exactly (test).
- Pure and deterministic: same inputs → identical output, no clock/network/file access
  (`ValuationDate` is an input).

## Error cases

| Error | Trigger |
|---|---|
| `InvalidAssumptionError` | g ≥ WACC, RR ≥ 1, negative WACC, out-of-domain override (each names the field and the constraint) |
| `InsufficientHistoryError` | <3 years reaching the engine (defense in depth; ingest should have caught it) |
| Warnings (not errors) | terminal-g override past `min(2.5%, 10Y)` · β=1.0 fallback in use · negative UFCF in explicit years · `cash_plug_negative` · `lease_heavy` · `growth_fade_steep` (FY1 default > 25%) · `interest_imputed` · `roic_fallback` (value-neutral terminal reinvestment) · TV > 85% of EV (info: "value is mostly terminal") · **all inherited ingest warnings pass through verbatim** (`coverage_low` and `immaterial_cash_residual` are mandatory in every output surface) |

## How tested

- **Hand-computed micro-case:** a synthetic 2-product toy history small enough to verify
  every projected line and the full DCF by hand; expected values derived in test
  comments, not from the code under test.
- **Property tests:** g = 0 ⇒ RR = 0 and TV = NOPAT_{N+1}/WACC · value monotonically
  decreasing in WACC and increasing in g · mid-year on/off differ by exactly
  (1+WACC)^0.5 on the explicit-period PVs when the stub is zero · sensitivity center
  cell equals base case.
- **Golden fixtures:** full ModelResult snapshots from committed history + market
  fixtures — MSFT first (the phase 2 deliverable), KO/COST/KHC added with the Excel
  phase; any change to outputs must be an intentional snapshot update with a review
  note.
- **Sanity check:** implied equity value within a plausible band of observed market
  cap for a mature fixture filer — not because the market is right, but because a
  value 10× off is a bug, not an insight.
- **Invariant tests:** P1–P4 asserted across all fixtures and randomized assumption
  overrides (hypothesis-style fuzzing within valid domains).
- **Excel parity:** spec 05's harness recalculates the workbook and diffs every output
  cell against `ModelResult` (rel. tol 1e-6) for all fixtures × toggle combinations.
