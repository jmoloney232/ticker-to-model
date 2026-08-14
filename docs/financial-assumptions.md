# Financial Assumptions — the Audit Guide

Every financial assumption in this codebase, what it does, where its number comes
from, and what a skeptical reviewer should press on. Written for two readers: the
owner, and anyone auditing the model from a financial standpoint.

**How this document relates to the code.** The canonical convention registry is
[`backend/engine/methodology.yaml`](../backend/engine/methodology.yaml) — it renders
the website's `/methodology` page and the workbook's Methodology sheet, so those
surfaces cannot drift from it. This document is the *narrative companion*: it walks
the full calculation chain in order and cites the implementing code. If this document
and the code ever disagree, the code and `methodology.yaml` win, and the discrepancy
is a bug worth reporting. Code references: derivations in
[`engine/assumptions.py`](../backend/engine/assumptions.py), statement mechanics in
[`engine/projections.py`](../backend/engine/projections.py), discounting/terminal
value/bridge in [`engine/dcf.py`](../backend/engine/dcf.py), cost of capital in
[`engine/wacc.py`](../backend/engine/wacc.py), reverse DCF in
[`engine/reverse.py`](../backend/engine/reverse.py), presets in
[`engine/presets.yaml`](../backend/engine/presets.yaml).

**How to audit any number.** Every figure in the dashboard and workbook traces back
in four hops, each visible in the product: (1) an output cell is a formula over
assumption cells; (2) every assumption shows its provenance — `derived`,
`preset:<name>`, or `user` — and its derivation sentence; (3) every derivation
consumes line items from the ingested history; (4) every ingested line item carries
the XBRL tag it came from, the SEC accession number, and whether it was restated.
There is no fifth place a number can come from.

**Notation.** FY0 = the latest filed fiscal year. "3y mean" = the arithmetic mean of
the *per-year* ratio over the last min(3, available) fiscal years — computed per year
and then averaged, so a mix-shift year stays visible rather than being blended away.
53-week fiscal years enter these means unadjusted (they are detected and annotated —
see §1). The forecast horizon is 5 years.

---

## 1. What the model is built on (data policies with financial content)

These are ingest-layer policies, but each changes the numbers the model sees:

- **Restatements: latest-filed wins.** For each (concept, period), the fact from the
  most recently filed accession is used — the *as-restated* basis, which is correct
  for forecasting. When the chosen value differs >1% from the originally filed one, a
  visible warning names the change (this is how KHC's restatement surfaces).
- **53-week years are annotated, never normalized.** A 53rd week inflates apparent
  growth by roughly 1/52 ≈ 1.9%. v1 discloses instead of adjusting (an adjustment
  would touch every line item for a ~2% effect).
- **Derived EBIT.** When a filer doesn't tag an operating-income subtotal, EBIT is
  derived as pretax income + interest, and the model carries a warning stating the
  absorbed non-operating magnitude.
- **No silent zeros.** A missing required item is a hard error. A missing optional
  item is logged with the tags that were tried, and enters the model as 0 only under
  a documented per-item rule (each such rule appears in §2/§4 below).
- **Coverage gate.** If less than 60% of assets *or* liabilities map to named line
  items, the model refuses to build and names the largest unattributed balances
  (Deere's captive-finance balance sheet fails here at ~20% mapped). Between 60–85%
  it builds behind a hard, non-dismissible warning (NVIDIA at ~73%).
- **Cash-reconciliation materiality band.** Historical cash flows must tie to the
  change in cash. An unreconciled residual below **1% of revenue AND 5% of gross
  cash flows** is quantified and disclosed per year; above either leg, the model
  refuses. (GE's spin year, at 1.27% of revenue, fails; Amazon's FX presentation
  quirks pass with disclosure.)
- **Banks, insurers, REITs** are detected by SIC code and declined — their statements
  don't have the structure this model assumes, and a wrong model is worse than none.

---

## 2. The derived assumption set

Every default is derived from the company's own filed history by the rules below.
Every one is editable; overrides are validated against the domain table in §2.9.
Provenance (`derived` / `preset:<name>` / `user`) travels with each field to the
dashboard, the API, and the workbook.

### 2.1 Revenue growth

| Field | Default | Rule |
|---|---|---|
| `revenue_growth_fy1` | 3y revenue CAGR, **capped at 30%** | `(rev_FY0 / rev_FY-3)^(1/3) − 1`, then `min(·, 0.30)` |
| `revenue_cagr_uncapped` | The uncapped CAGR | Display-only, so the cap hides nothing |

Growth then **fades linearly from FY1 to terminal g by FY5** (§4.1). The cap exists
because "the company grew 60%, so we assumed 60%" is not a derivation anyone can
defend; the uncapped figure is displayed beside it, and overriding upward is allowed.
A soft warning fires above 25% (`growth_fade_steep`) — even fading linearly, the
cumulative five-year path is aggressive.

### 2.2 Operating costs and margins

Cost lines are projected **D&A-inclusive, as filed** — filers embed depreciation
inside COGS/SG&A and don't split it out, so projecting D&A-inclusive ratios *and* a
separate D&A line would double-count. EBIT is therefore an identity:
`EBIT = revenue − Σ(cost lines)`. The D&A used for cash flow, the PP&E roll, and
EBITDA is a *memo* line (§2.3), never subtracted from the P&L again.

| Field | Default | Notes |
|---|---|---|
| `cogs_pct` | 3y mean cost of revenue / revenue | Only for `by_function` filers. By-nature filers (VZ, DAL, MCD) have no COGS; the model branches on the filer's stated `cost_structure` |
| `rnd_pct` | 3y mean R&D / revenue | |
| `sga_pct` | 3y mean SG&A / revenue | |
| `other_opex_pct` | 3y mean other operating / revenue | |
| `unclassified_costs_pct` | 3y mean of `(revenue − EBIT − Σ named cost lines) / revenue` | **The margin-identity closure.** See below |
| `sbc_pct` | 3y mean stock compensation / revenue | 0 when unmapped, with a warning |

**The margin-identity closure deserves an auditor's attention.** Operating costs that
live in XBRL tags the schema doesn't map are still real costs. This line projects
them explicitly, which forces the projected EBIT margin to reproduce the filer's own
historical margin structure *by identity*. For most filers it derives to ~0 and is
inert. Where it isn't (McDonald's company-operated restaurant costs, ~$11.5B, sit in
tags outside the named set), omitting it projected an 89% EBIT margin against a real
46%. Above 1% of revenue, a warning names the percentage so those filers are
findable; the honest label "unclassified" is deliberate — a wrong "SG&A" would be
worse than an unnamed line.

### 2.3 Capital intensity

| Field | Default | Notes |
|---|---|---|
| `capex_pct` | 3y mean capex / revenue | |
| `da_pct_beginning_ppe` | 3y mean D&A / **beginning** net PP&E | The memo-D&A rate. Falls back to `da_pct_revenue` (3y mean D&A / revenue, disclosed) if PP&E is unmapped |

PP&E rolls forward as `PP&E_t = PP&E_{t−1} + capex_t − D&A_t`. Disclosed nuance: the
D&A *embedded* in the cost ratios follows revenue, while the *memo* D&A follows the
capex path — the two can drift in later years, so late-year EBITDA margins shift
slightly rather than staying pinned. This is a property of the D&A-inclusive
convention, not an error.

### 2.4 Working capital

| Field | Default | Notes |
|---|---|---|
| `dso` | 3y mean of `365 × AR / revenue` | days |
| `dio` | 3y mean of `365 × inventory / cost basis` | days |
| `dpo` | 3y mean of `365 × AP / cost basis` | days |
| `oca_pct` | 3y mean other current assets / revenue | |
| `accrued_pct` | 3y mean accrued liabilities / revenue | |
| `ocl_pct` | 3y mean other current liabilities / revenue | |
| `defrev_pct` | 3y mean current deferred revenue / revenue | |

**Cost basis for DIO/DPO:** COGS for by-function filers. By-nature filers have no
COGS, so total operating costs (`revenue − EBIT`) substitute. This is safe *inside
the model* because the identical denominator drives both the historical ratio and
the projection — the distortion cancels — but the resulting DIO/DPO are projection
ratios, **never comparable to another company's disclosed days**. The UI and
derivation strings say so.

Net working capital = AR + inventory + other current assets − AP − accrued − other
current liabilities − current deferred revenue. Cash, short-term investments, and
all debt are excluded — NWC here is an *operating* concept; financing lives in the
capital structure.

### 2.5 Taxes

| Field | Default | Notes |
|---|---|---|
| `effective_tax_fy1` | 3y mean of `income tax / pretax income`, **excluding loss years**, clamped to [10%, 35%] | Falls back to marginal if all years are losses |
| `marginal_tax` | 25% (21% federal + state blend) | Editable |

The tax rate **fades linearly from effective (FY1) to marginal (FY5)**. The marginal
rate is used in three places that must agree — terminal NOPAT, the after-tax cost of
debt, and the pension bridge item — because taxing the perpetuity at one rate and
the debt shield at another is a classic silent error. The engine *asserts* this
consistency rather than trusting it. NOLs and deferred-tax modeling are out of scope
and disclosed.

### 2.6 Financing lines

| Field | Default | Notes |
|---|---|---|
| `payout_ratio` | 3y mean dividends / net income over positive-NI years, clamped [0, 1] | Dividends are paid only on positive projected NI |
| `embedded_debt_rate` | 3y mean interest expense / **beginning** gross debt | The legacy-coupon rate; drives *P&L* interest only. The WACC uses the marginal (synthetic) rate — different questions, both documented |
| `interest_income_yield` | 3y mean interest income / beginning (cash + ST investments), clamped [0, risk-free] | 0 when unmapped |
| `coverage_ratio` | `Σ3y EBIT / Σ3y interest expense` | Ratio of *sums*, not mean of ratios — a near-zero interest year would explode a per-year ratio. `None` when no interest is traceable |

**The unobservable-interest asymmetry (deliberate, both directions conservative):**
some filers (AAPL-shape) stopped tagging interest lines separately. If interest
*expense* is unmapped while material debt exists, the rate is **imputed at the
synthetic Kd** with a warning — projecting $0 interest on $90B of real debt would
fabricate pretax income. If interest *income* is unmapped, it stays **0** with a
warning — inventing income on cash would inflate the model. Omit unobservable
income; impute unobservable expense.

### 2.7 Cost-of-capital inputs

| Field | Default | Notes |
|---|---|---|
| `risk_free` | Spot 10Y Treasury (FRED DGS10) | Staleness labeled when served from cache; normalized-rate override supported |
| `erp` | 5.0% | **A house combination, named as such.** The published packages are matched ERP/risk-free *pairs*: Damodaran's implied US ERP 4.23% (Jan 2026) paired with the spot 10Y; Kroll's recommended 5.0% (reaffirmed Jan 2026) paired with the higher of a normalized 3.5% or the spot 20Y. The engine default takes Kroll's level with Damodaran's risk-free convention — defensible, but a third combination neither source publishes. The `damodaran_implied` preset applies the Damodaran package as published (§9) |
| `beta` | 2y weekly OLS vs SPY, **Blume-adjusted** (⅔β + ⅓) | Computed in-house from split-adjusted bars; ≥80 paired weekly observations required, else β = 1.0 fallback with a loud warning |
| `beta_raw` | The unadjusted regression beta | Display + toggle |

Weekly frequency avoids the non-synchronous-trading bias that drags daily betas
toward zero; the 2-year window keeps recency (Bloomberg's default; citable). The
Blume adjustment anticipates mean reversion. **Disclosed limitation:** a regression
beta embeds the company's *current* leverage — if your overrides materially change
the capital structure, the beta is silently inconsistent (unlever/relever is a
documented v2 extension).

### 2.8 Terminal-value and bridge inputs

| Field | Default | Notes |
|---|---|---|
| `terminal_growth` | `max(1.5%, min(2.5%, 10Y yield))` | **A house cap, and a deliberate deviation:** the published rule (Damodaran) is `g ≤ rf` — the risk-free rate embeds long-run growth and inflation assumptions the cash flows should share. The engine caps at 2.5% anyway, as a conservative stance, and displays the current 10Y beside the default (`terminal_growth_rf_ceiling`) so the cap hides nothing; in the current rate environment the two differ materially. The floor exists because unfloored `g ≤ rf` breaks in distorted-rate regimes (2020: g ≈ 0.7% would have cut every valuation 20–30% for non-business reasons). Warnings: stated g above **rf** draws P5 (the published constraint); above the house cap but ≤ rf draws only an info flag; `g ≥ WACC` is hard-blocked |
| `terminal_roic` | 3y mean of `NOPAT_marginal / beginning invested capital`, where IC = gross debt + stockholders' equity + NCI + preferred + temporary equity − cash − ST investments | `None` (degenerate history) → falls back to ROIC = WACC with a warning: reinvestment is value-neutral because returns could not be estimated |
| `exit_multiple` | Current EV / FY0 EBITDA, where EV = market cap + gross debt − cash − ST investments and EBITDA = EBIT + D&A | `None` when FY0 EBITDA ≤ 0 (the exit leg is then unavailable). Assumes today's pricing persists — circular-ish, labeled as such, and disciplined by the implied cross-check (§6.3) |
| `share_count` | Current cover-page basic shares × latest-FY (diluted WA ÷ basic WA) ratio | See §7.2 |
| `cash_floor_pct` | 2% of revenue | Cash below the floor is operating, not excess (§7.1) |

### 2.9 Toggles and override domains

Four flags: `midyear` (on), `sbc_addback` (off — SBC stays an expense),
`kd_synthetic` (on), `beta_adjusted` (on — Blume). Each is explained where it binds
(§5, §6).

Overrides are validated at entry against hard domains — FY1 growth ∈ [−50%, +100%],
terminal g ∈ [−2%, 10%], tax rates ∈ [0%, 60%], payout ∈ [0, 1], beta ∈ (0, 4],
ERP ∈ [1%, 12%], risk-free ∈ [0%, 15%], multiple ∈ (0, 100], ROIC ∈ (0, 200%],
cash floor ∈ [0%, 25%]. These catch nonsense; *economic* constraints (g < WACC,
reinvestment rate < 1) are enforced at build time and block with the reason. Note
the terminal-g domain deliberately admits values the *default* would never produce
(negative g for a decliner-in-perpetuity view; up to 10% because market-implied
solves legitimately land there) — the domain bounds inputs, the warnings and hard
blocks police economics.

---

## 3. What the projections hold fixed (and say so)

The following balance-sheet lines are **held flat at FY0** for all five years: short-
and long-term debt, operating lease liabilities and ROU assets, goodwill,
intangibles, short- and long-term investments, combined-unsplit investments,
deferred tax liabilities, pension liabilities, other noncurrent items, NCI,
preferred, and temporary equity. No buybacks; the share count is constant. This is a
stated modeling choice, not an omission: no defensible history-derived growth rule
exists for these lines in v1, and an explicit flat line is inspectable where a
silent default is not. The financial consequences an auditor should note:

- **Debt is constant**, so P&L interest expense is constant (embedded rate × FY0
  gross debt). There is no revolver: a plan that runs cash negative raises a
  `cash_plug_negative` warning instead of fabricating financing.
- **Goodwill flat** means no projected impairments and no acquisitions — the model
  values the business as filed, organically.
- One additional flat line, `unattributed_carryforward`, carries the FY0 mapping
  residual (real filers' mapped components never sum *exactly* to reported totals —
  the residual is within the validated H1 tolerance). It is visible in every
  projected balance sheet rather than being silently absorbed into the plug.

**Cash is the balance-sheet plug**: after every other line is set by its stated
rule, cash = liabilities + equity − other assets. Equity rolls as
`equity_{t−1} + NI − dividends + SBC` (SBC credits equity — it is an expense in the
P&L and a non-cash credit in the roll, which is exactly how dilution shows up
without modeling share issuance). The projected balance sheet balances and the cash
flow statement ties to Δcash **exactly, by construction** — and the engine asserts
both anyway (P1, P2) as bug tripwires.

Other non-operating income is projected at **zero**: one-offs are not run-rate, and
the historical columns display them where they occurred.

---

## 4. Free cash flow

```
UFCF = EBIT × (1 − tax_t) + D&A [+ SBC if toggled] − capex − ΔNWC
```

- `tax_t` is the fading tax path (§2.5), applied to EBIT — unlevered FCF pairs with
  WACC; the P&L's interest lines affect net income and the cash plug but never UFCF.
- **SBC is expensed by default — not added back.** Street practice adds it back
  (higher FCF); treating it as a real expense (the Damodaran position) is
  conservative and honest about dilution. Expect values to read *low* versus typical
  sell-side DCFs for heavy-SBC issuers — that is the convention working, not a bug.
  The toggle (`sbc_addback`) re-adds it as non-cash for comparability.

**Discounting.** The valuation date is an explicit input (never `TODAY()` in the
workbook). With `stub` = elapsed fraction of a year since FYE0, the FY_i cash flow
discounts at exponent `t_i = i − stub − 0.5·midyear`. Mid-year discounting (default
on, worth ~2–4% of value) reflects cash arriving through the year rather than on the
last day. When the valuation date equals FYE0 and mid-year is off, the model
reproduces the textbook year-end DCF exactly — a tested invariant. A valuation date
more than a year past FYE0 draws a `history_stale` warning instead of silently
producing stale numbers.

---

## 5. Cost of capital (WACC)

```
Ke   = rf + β × ERP                                  (CAPM)
Kd   = rf + spread(rating)      [synthetic, default]  or embedded rate [toggle]
WACC = We × Ke + Wd × Kd × (1 − marginal tax)
```

- **Beta selection order:** user override → preset value → (no market beta: 1.0
  fallback, warned) → Blume-adjusted (default) or raw (toggle).
- **Synthetic rating:** 3y interest coverage (§2.6) maps to a rating and default
  spread through Damodaran's **large non-financial firms** table (data as of
  **2026-01** — a `rating_table_stale` flag fires when the table is >18 months
  older than the valuation date; a parity test asserts the engine's constants
  match `methodology.yaml` exactly):

  | Coverage > | Rating | Spread |
  |---|---|---|
  | 8.50 | Aaa/AAA | 0.40% |
  | 6.50 | Aa2/AA | 0.55% |
  | 5.50 | A1/A+ | 0.70% |
  | 4.25 | A2/A | 0.78% |
  | 3.00 | A3/A− | 0.89% |
  | 2.50 | Baa2/BBB | 1.11% |
  | 2.25 | Ba1/BB+ | 1.38% |
  | 2.00 | Ba2/BB | 1.84% |
  | 1.75 | B1/B+ | 2.75% |
  | 1.50 | B2/B | 3.21% |
  | 1.25 | B3/B− | 5.09% |
  | 0.80 | Caa/CCC | 8.85% |
  | 0.65 | Ca2/CC | 12.61% |
  | 0.20 | C2/C | 16.00% |
  | (below) | D2/D | 19.00% |

  The distressed brackets run all the way to D — truncating them would understate
  the cost of debt (and overstate value) exactly for the companies most likely to
  be worth nothing. At or above the Caa/CCC bracket, a
  `synthetic_rating_distressed` warning states that both the synthetic-rating
  method and the going-concern DCF framing are under strain, and points at the
  reverse-DCF recovery view. No traceable interest → the top bracket, disclosed.
  **Disclosed limitation:** synthetic rating is a fallback method intended for
  *unrated* issuers; most companies in this universe carry an actual agency
  rating the engine does not yet ingest (known-limitations, v2). Why synthetic
  over embedded by default: the embedded coupon measures *legacy* debt — 3% notes
  issued in 2021 against a 7% refi environment make financing look far cheaper
  than it is. The synthetic rate estimates the *marginal* cost with no new data.
  The embedded rate remains available as a toggle (and silently falls back to
  synthetic, disclosed, when no coupon is observable).
- **Weights: equity at market cap** (price × the §7.2 share proxy), **debt at gross
  book value** including finance leases. Book debt is the standard proxy for
  investment-grade names when bond prices aren't available. **Net debt appears only
  in the EV→equity bridge — never in the weights.** Using net in both is the single
  most common WACC error; here it is a *tested structural invariant* (P3), not a
  convention a user can break.
- The after-tax Kd uses the same `marginal_tax` field as terminal NOPAT — asserted
  in code (§2.5).

---

## 6. Terminal value — two methods, deliberately asymmetric

### 6.1 Gordon (perpetuity growth) with reinvestment consistency

```
NOPAT_{N+1} = EBIT_FY5 × (1 + g) × (1 − marginal tax)
RR          = g / ROIC_t                                (must be < 1)
TV          = NOPAT_{N+1} × (1 − RR) / (WACC − g)
```

Growing year-5 FCF at g forever — the common shortcut — implicitly assumes new
growth requires *no* reinvestment, i.e. infinite marginal ROIC, quietly inflating TV
exactly where TV is 75%+ of the value. Tying the reinvestment rate to `g / ROIC_t`
makes growth cost what it should. ROIC_t resolution: an explicit user/preset value
that violates `ROIC > g` is **rejected** (their statement, their constraint); a
degenerate *derived* value falls back to ROIC = WACC with a warning — reinvestment
earning exactly its cost adds zero value from growth, the honest neutral stance when
history can't support an estimate.

Discounting: with mid-year on, the Gordon TV discounts at `t_N − 0.5` — perpetual
flows arrive *through* each year.

### 6.2 Exit multiple

```
TV = exit_multiple × EBITDA_FY5        (EBITDA = EBIT + memo D&A)
```

The default multiple is the company's **own current EV/EBITDA** (§2.8) — comps are
out of scope in v1, and importing a hand-picked peer multiple would smuggle in an
undocumented assumption. Discounting: at **full `t_N` regardless of the mid-year
toggle** — a sale is a point-in-time, year-end event, not a flow. This asymmetry
with the Gordon leg is deliberate and unit-tested.

### 6.3 The cross-check that disciplines both

Displayed always, both directions: `implied exit multiple = TV_gordon / EBITDA_FY5`
and `implied g = WACC − FCF_terminal / TV_exit` (a labeled approximation — it holds
the Gordon leg's reinvestment fixed). "Your 18× exit implies g = 3.1%; your 2.5% g
implies 14.4×." Inconsistent terminal assumptions become visible instead of
disclaimed. The same closed forms live in the engine and the workbook, covered by
the parity test.

### 6.4 When a leg refuses to exist

A perpetuity on a negative base, or a multiple of negative EBITDA, is a sign error
dressed as a number. **Gordon is unavailable when terminal NOPAT ≤ 0; exit is
unavailable when projected FY5 EBITDA ≤ 0** — each with a warning naming the anchor,
each rendered as a reasoned "unavailable" state, never an error and never a negative
perpetuity. A *positive-but-tiny* anchor is deliberately not guarded: KHC after its
impairment window produces a Gordon value of −$11.55/share — that is EV below gross
debt, a legitimate statement that the equity is worth ~nothing at these assumptions,
and it prints as such. The reverse DCF (§8) stays available either way, because the
implied recovery view is most informative exactly when the forward model refuses.

### 6.5 Sensitivity grids

WACC × terminal g: **5 × 9** (WACC ±1.0% at 0.5% steps; g ±1.0% at 0.25% steps —
value is convex in g approaching WACC, so coarse steps are least informative exactly
where the grid matters; a downside-biased span was considered and rejected as a
stance the grid shouldn't silently take). WACC × exit multiple: **5 × 5** (±2.0× at
1.0×). Every cell is a **full re-computation** — the g grid re-projects the entire
forecast per column because the growth path fades *into* terminal g; nothing is
interpolated. Cells where g ≥ WACC are blank, not extrapolated. The base case is the
ringed center cell of each grid.

---

## 7. From enterprise value to a share price

### 7.1 The EV → equity bridge

```
Equity value = EV + excess cash + LT investments − gross debt − NCI − preferred
               − temporary equity − unfunded pension × (1 − marginal tax)
```

| Item | Treatment | Why |
|---|---|---|
| Excess cash | cash + ST investments **above a 2%-of-revenue operating floor** | Treating *all* cash as excess flatters cash-rich names; the floor is a disclosed heuristic, editable |
| LT investments | added at book | Non-operating assets the DCF's FCF never sees |
| Gross debt | subtracted (ST + LT, incl. finance leases) | Net debt appears only here — see P3 |
| NCI | subtracted at book | Fair value needs segment multiples — out of scope, disclosed proxy |
| Preferred equity | subtracted at book | Senior claim |
| Temporary equity | subtracted | Redeemable NCI — senior to common |
| Unfunded pension | subtracted **after-tax at marginal** | Contributions are deductible; usually unmapped (warned), never a silent zero |

Each component carries its ingest source, so "0 — unmapped" renders as exactly that,
never as a bare zero.

**Operating leases are excluded from debt** (finance leases are in). Including them
would require lease-adjusting EBITDA too; v1 keeps both sides of the ratio
consistent by excluding both. When the lease liability exceeds **25% of gross
debt** — retail, restaurants, airlines — the model surfaces a warning (P6) because
the two treatments genuinely diverge there and a footnote isn't sufficient.
**Combined-unsplit investment totals** (NVIDIA-style single tags mixing current and
noncurrent) are excluded from net debt by default, with disclosure.

### 7.2 Share count

`Current cover-page basic shares × latest-FY (diluted WA / basic WA)`. Equity value
per share is a *point-in-time* measure — it needs today's diluted count, and a
weighted average over a finished period systematically understates the denominator
for steady issuers. Full treasury-stock-method dilution needs option/RSU footnote
data (out of scope v1), so the latest observed dilution ratio applied to the current
basic count is the documented proxy. Where share tags are dimensional (GOOGL, META
dual-class), weighted-average counts are derived as NI ÷ EPS and always warned —
per-share provenance stays visible.

---

## 8. Reverse DCF

For four levers — terminal growth, FY1 revenue growth, EBITDA margin, capex % — the
engine bisects the assumption until the Gordon leg equals the current market price,
holding every other default fixed: "what you'd have to believe." Outcomes are
`solved`, `no_solution_below_wacc` (no g < WACC reaches the price — common for richly
priced names), or `no_solution_in_range` — reported in words, never dressed as a
number. The dashboard's hero is the terminal-growth solve: assumed 2.50% vs
market-implied, gap in percentage points.

---

## 9. Assumption presets — stated methodologies, not moods

A preset transforms derived defaults by named rules; it never replaces the
derivation, never bypasses domain or model validation (g ≥ WACC still blocks,
whichever preset asked), and never suppresses warnings. An inapplicable preset says
so with the reason — `market_implied` reports "no solution below WACC" rather than
quietly moving a different lever.

| Preset | Rules (verbatim) |
|---|---|
| `derived` | The identity case — every field keeps provenance `derived` |
| `market_implied` | `terminal_growth` solved so Gordon = market price (§8) |
| `street_convention` | `terminal_growth = max(1.5%, 10Y)` (ceiling lifted to the 10Y itself); `capex_pct = (capex_pct + historical D&A%)/2` (midpoint of a fade to maintenance parity); `effective_tax_fy1 = marginal_tax` (marginal from FY1) |
| `damodaran_implied` | The Damodaran cost-of-equity package as published, not mixed: `erp = 4.23%` (implied US ERP, Jan 2026 — a literal with its source and as-of in provenance), paired with the spot 10Y the engine already uses; `terminal_growth = min(nominal-GDP proxy 4.0%, 10Y)`. The GDP proxy is an editable constant in `methodology.yaml`, parity-tested against the engine |
| `downside` | Full cost stack from the **worst-EBIT-margin year** in the window; FY1 growth premium over terminal g halved (faster fade, never raised for decliners); `beta = max(raw β, 1.0)` — the Blume benefit declined and sub-market betas stressed to 1 (correlations rise toward 1 in drawdowns) |

---

## 10. Validation — what is checked on every build

| Check | Severity | What it asserts |
|---|---|---|
| P1 | fail | Projected balance sheet balances every year (exact by plug construction, asserted anyway) |
| P2 | fail | Projected cash flow ties to Δcash every year |
| P3 | fail | Gross debt in the WACC weights; net debt only in the bridge |
| P4 | fail | g < WACC; WACC > 0; shares > 0; reinvestment rate < 1 |
| P5 | warn | Any *stated* (user or preset) terminal g above `min(2.5%, 10Y)` — provenance named |
| P6 | warn | Operating lease liability > 25% of gross debt (the lease-exclusion convention materially binds) |
| P7 | warn | Model-quality flags: negative cash plug, negative UFCF years, beta fallback |
| P8 | info | Terminal value share of EV; flagged above 85% — the explicit forecast barely matters and the terminal assumptions carry the valuation |

Alongside the checks, every warning inherited from ingest (restatements, 53-week
years, unmapped optional items, coverage) and market data (staleness, beta fallback)
travels to the dashboard, the API, and the workbook cover — structured, and not
droppable anywhere in the pipeline.

---

## 11. What an auditor should press on (the honest list)

Documented limitations, each deliberate, each with its rationale — challenge them in
this order:

1. **The linear growth fade.** FY1 → terminal g in a straight line is transparent
   but front-loads growth for high-growth names; the *cumulative* five-year path can
   be aggressive even when each year looks tame (warned above 25% FY1). A curved
   fade is the documented v1.1 candidate.
2. **Terminal assumptions dominate.** P8 tells you when TV is >85% of EV — at that
   point §6's choices (g, ROIC_t, the multiple) *are* the valuation. The
   cross-check makes inconsistency visible; it cannot make the terminal knowable.
3. **Beta embeds current leverage** (§2.7) and the **ERP is a stated 5%** — both
   editable, neither observable. CAPM itself is the convention, not a truth.
4. **Held-flat lines** (§3): constant debt, no buybacks, goodwill frozen. For serial
   acquirers or heavy repurchasers, the model is valuing a stylized organic version
   of the company — the workbook makes this visible line by line.
5. **The exit multiple is the company's own** — today's pricing assumed to persist
   (labeled; disciplined by the implied-g cross-check, §6.3).
6. **Operating leases excluded** from debt and EBITDA both — consistent, but for
   lease-heavy names (P6 warns) an adjusted-basis view would move the answer.
7. **By-nature DIO/DPO** are projection ratios, not comparable days (§2.4).
8. **SBC expensed** reads conservative vs street models (§4) — a feature, but know
   it when comparing outputs.
9. **53-week years unnormalized**; ~1.9% growth distortion in affected transitions,
   annotated.
10. **NOLs, segments, quarterly data, comps, revolver, unlever/relever — out of
    scope v1**, all disclosed in [`docs/known-limitations.md`](known-limitations.md),
    which also catalogs the filers that refuse to build and why.

Everything in this list is visible in the product — as a warning, a check, a
derivation string, or an unavailable state. The model's core commitment is that
nothing on it is silent.
