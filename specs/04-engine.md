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

## Assumption defaults and derivation rules

All derived from the company's own history; all editable; all shown with their
derivation text in the UI and workbook.

| Assumption | Default derivation |
|---|---|
| Revenue growth, FY1–FY5 | Trailing 3y revenue CAGR, faded linearly to terminal g by FY5 |
| Gross margin | 3y average of gross profit / revenue |
| R&D % of revenue | 3y average (0 if company reports none) |
| SG&A % of revenue | 3y average |
| Other operating % of revenue | 3y average of residual operating items |
| D&A % of beginning net PP&E | 3y average (simplification: total D&A driven off PP&E; disclosed) |
| Capex % of revenue | 3y average |
| DSO / DIO / DPO | 3y average of 365·AR/revenue, 365·inventory/COGS, 365·AP/COGS |
| Other current assets / liabilities % of revenue | 3y average |
| Deferred revenue % of revenue | 3y average (0 if absent) |
| Effective tax rate (FY1) | 3y average effective rate, clamped to [10%, 35%] |
| Marginal tax rate | 25% flat (21% federal + state blend); terminal + Kd rate |
| Dividend payout ratio | 3y average dividends / net income, clamped to [0, 1] |
| Interest income yield on cash | 3y average interest income / avg (cash + ST investments), clamped to [0, rf] |
| Beta | Blume-adjusted from spec 03 (toggle: raw; manual override allowed) |
| Equity risk premium | 5.0% |
| Risk-free rate | Spot DGS10 (override for a normalized rate) |
| Terminal growth g | `max(1.5%, min(2.5%, 10Y yield))` — floor prevents distorted-rate regimes from mechanically crushing valuations |
| Terminal ROIC (`ROIC_t`) | Trailing 3y ROIC = NOPAT / invested capital (gross debt + total equity − cash − ST investments) |
| Exit EV/EBITDA multiple | Company's own current EV / trailing EBITDA (labeled: assumes today's multiple persists) |
| Operating cash floor | 2% of revenue (cash below this is operating, not excess) |
| Toggles | Mid-year discounting (default on) · SBC add-back (default off = expensed) · Kd method (default synthetic) · beta adjustment (default Blume) |

## Projection mechanics (FY1–FY5)

Three full statements, built so the exported workbook needs **no circular references**:

- **Income statement:** revenue from growth path; COGS/opex from ratio assumptions;
  D&A = rate × *beginning* net PP&E; **interest expense = embedded historical rate ×
  beginning gross debt** (debt held constant — see financing policy); **interest income
  = yield assumption × beginning (cash + ST investments)**; taxes at the effective→
  marginal fade (linear across FY1–FY5, hitting marginal in the terminal year).
  Note the deliberate split: projected P&L interest uses the *embedded* rate (cost of
  the legacy debt actually outstanding); the *WACC* uses the marginal rate (synthetic
  rating) — different questions, different rates, both documented.
- **Balance sheet:** AR/inventory/AP from DSO/DIO/DPO; other WC items % of revenue;
  net PP&E rolls `begin + capex − D&A`; goodwill/intangibles held flat (no acquisitions
  modeled); debt constant; equity rolls `begin + NI − dividends`; **cash is the plug**
  (no revolver in v1 — acceptable for the large-cap non-financial universe; a deeply
  negative cash plug raises a `cash_plug_negative` warning rather than fabricating a
  revolver).
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

Missing optional bridge items are logged, never silently zero. Operating leases stay
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
| Warnings (not errors) | terminal-g override past `min(2.5%, 10Y)` · β=1.0 fallback in use · negative UFCF in explicit years · `cash_plug_negative` · `lease_heavy` · TV > 85% of EV (info: "value is mostly terminal") |

## How tested

- **Hand-computed micro-case:** a synthetic 2-product toy history small enough to verify
  every projected line and the full DCF by hand; expected values derived in test
  comments, not from the code under test.
- **Property tests:** g = 0 ⇒ RR = 0 and TV = NOPAT_{N+1}/WACC · value monotonically
  decreasing in WACC and increasing in g · mid-year on/off differ by exactly
  (1+WACC)^0.5 on the explicit-period PVs when the stub is zero · sensitivity center
  cell equals base case.
- **Golden fixtures:** full ModelResult snapshots for MSFT/KO/COST/KHC from committed
  history + market fixtures; any change to outputs must be an intentional snapshot
  update with a review note.
- **Invariant tests:** P1–P4 asserted across all fixtures and randomized assumption
  overrides (hypothesis-style fuzzing within valid domains).
- **Excel parity:** spec 05's harness recalculates the workbook and diffs every output
  cell against `ModelResult` (rel. tol 1e-6) for all fixtures × toggle combinations.
