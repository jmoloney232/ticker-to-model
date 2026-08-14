# Spec 07 — Validation

Validation is a first-class feature (non-negotiable #3): never silently produce a model
that doesn't tie. This spec owns the check definitions, tolerances, severities, and
surfacing. Checks run in two places: the **historical gate** inside ingest (spec 01,
step 8) and the **projection invariants** inside the engine (spec 04).

## Inputs

- Assembled historical statements (pre-gate, from ingest)
- Projected statements + DCF internals (from engine)
- Tolerances and thresholds (constants defined here, mirrored in methodology.yaml)

## Outputs

`ValidationReport`: list of check results `{check_id, severity, status, magnitude,
tolerance, detail, per_period}` + overall status (`pass` / `pass_with_warnings` /
`fail`). Attached to `FinancialHistory` and `ModelResult`; rendered as the dashboard
banner (spec 06) and the workbook Cover table (spec 05).

## Historical checks (per fiscal year)

| ID | Check | Severity |
|---|---|---|
| H1 | **Balance sheet balances:** total assets = total liabilities + total equity (incl. NCI + preferred) | fail |
| H2 | **Cash flow ties:** CFO + CFI + CFF + FX effect = Δcash. **Four distinguishable per-year outcomes (owner decisions), recorded structurally in `CheckResult.outcomes` — never collapsed into one warning type:** `tie` — reconciles within tolerance; `definitional` — ties only under a broader cash definition (restricted cash / disposal-group variants), or the filer's reported net-change ties while our narrower BS cash delta does not (verified WMT); `immaterial` — an unreconciled residual survives every basis but is below **both materiality legs (1% of revenue AND 5% of gross \|CFO\|+\|CFI\|+\|CFF\|, owner-approved 2026-08-13)** — quantified per year in dollars and both percentages, disclosed in the detail and as an `immaterial_cash_residual` warning (the auditor's treatment; verified AMZN/TSLA/F/DIS at ≤0.73% of revenue); `fail` — the residual exceeds a leg: a real break (verified GE FY2022, 1.27% of revenue). The materiality test covers both the Δ-side residual and the internal \|flows − reported net change\| residual — the same FX/restricted-cash quirk breaks both by the same amount on the verified filers, and a materially inconsistent CF statement fails on either | fail |
| H3 | **Net income consistency:** `NetIncomeLoss` + NCI income = `ProfitLoss` where both were reported. Honest note: `companyfacts` facts are statement-agnostic, so the classic "NI matches between IS and CF" is reframed as tag-level consistency (H3) plus the roll-forward (H4) | fail |
| H4 | **Retained-earnings roll-forward (soft):** RE_t ≈ RE_{t−1} + NI_t − dividends_t. Legitimately noisy (share retirements, some OCI reclassifications, ASU adoptions hit RE directly) → warn-only | warn |
| H5 | **Schema cross-checks** (from spec 02): reported vs derived gross profit; reported vs derived total liabilities; sum of mapped current assets vs reported total | warn |
| H6 | **Restatement delta** >1% between latest-filed and first-filed value (set in ingest, reported here). Share/EPS-unit recasts are excluded — they are split adjustments (owner decision; NVDA 10:1 verified), labeled `split_adjustment` in warnings | warn |
| H7 | **53-week year** detected | info |

**Tolerances (H1, H2, H3):** `max($1M, 0.1% of total assets)`. EDGAR facts are exact
dollars, but composite/derived items (schema `derive:` rules) introduce residues;
below this threshold is mapping noise, above it is a real problem. **H4:** warn when
the residual exceeds 5% of total equity. **H5:** warn above the H1 tolerance.
**H2 materiality band** (between tolerance and failure): `H2_MATERIALITY_REV = 1%`
of the year's revenue and `H2_MATERIALITY_FLOWS = 5%` of the year's gross flows —
both must hold. Calibrated on the 29-ticker scan so real presentation quirks
($0.1–1.3B against hundreds of billions of revenue) are disclosed while GE's
structural spin-year break still fails.

A `fail` blocks the model: ingest raises `ValidationError` carrying the report — the
user sees which identity broke, by how much, in which year, with per-item provenance
(tag + accession) so the failure is diagnosable, not just loud.

## Plausibility checks (PL1–PL8) — historical, warn-only

A different failure class from the tie-outs: **arithmetic checks cannot see
misclassification**, because residual buckets keep statements balanced no matter
where a value lands (the KHC long-term-debt gap balanced perfectly while $20B sat
in a residual). PL checks look for **asymmetric presence** — an item that implies
another item exists, where the other resolved to zero. Always warnings, never
errors: they flag combinations for human review. Rules are one-directional; the
reverse implication usually has legitimate cases (noted per rule).

| ID | Rule | Reasoning / false-positive notes |
|---|---|---|
| PL1 | Material interest expense, zero gross debt | Paying interest implies borrowings. Reverse not flagged (zero-coupon, capitalized interest). FP risk: interest on uncertain tax positions — floored by tolerance |
| PL2 | D&A or capex, zero PP&E + intangibles + ROU | Depreciation needs a depreciable base; goodwill excluded (not amortized). All three bases must be zero, so intangible-only amortizers stay quiet |
| PL3 | Material revenue, COGS ≤ 0 | Selling something costs something — for `by_function` filers. Gated on `cost_structure` (reports `skipped` for by_nature filers, who have no COGS concept); catches tagged zeros on filers that should have one |
| PL4 | Debt issued/repaid in financing, debt never on any balance sheet | Repaying debt implies debt existed. FP: commercial paper churned intra-year never shows at an FYE — one reason this is warn-only |
| PL5 | Lease cost (probe tags), zero lease liability and ROU | ASC 842 books lease balances; guarded to periods ending ≥2020 (pre-842 operating leases were legitimately off-BS) |
| PL6 | Material tax expense, zero DTL + DTA + taxes payable (probes) | Tax expense implies a deferred position or payable. Highest FP risk of the set (payables fold into accrued) — review-level by design |
| PL7 | ROU asset XOR lease liability | ASC 842 books them together; a hard zero on one side is a mapping gap, not economics |
| PL8 | Revenue every year, AR zero every year | Accrual revenue leaves receivables; persistent zero suggests an unchained AR tag. Single-year zeros stay quiet |

Materiality floor for "present": the H-check tolerance `max($1M, 0.1%·assets)`.
Probe tags (lease cost, deferred tax assets, taxes payable) are read raw by the
mapper (`PLAUSIBILITY_PROBES`) — they are check inputs, not canonical items.
Empirical baseline: zero PL false positives across the MSFT/KO/COST/KHC fixtures.

## Projection & valuation checks

| ID | Check | Severity |
|---|---|---|
| P1 | Projected BS balances every year (exact — float tolerance 1e-8 relative) | fail (engine bug) |
| P2 | Projected CF ties to Δcash every year (exact) | fail (engine bug) |
| P3 | Gross debt in WACC weights; net debt only in the EV→equity bridge | fail (engine bug) |
| P4 | `g < WACC` and `g/ROIC_t < 1` | fail (rejects the input) |
| P5 | Terminal-g override above `min(2.5%, 10Y)` | warn |
| P6 | Operating lease liability > 25% of gross debt (`lease_heavy`) | warn |
| P7 | β fallback (1.0) in use; or negative UFCF in explicit years; or negative cash plug | warn |
| P8 | TV > 85% of enterprise value ("value is mostly terminal — explicit forecast barely matters") | info |

P1–P3 failing means the engine itself is broken — they exist as tripwires (assertions +
reported checks), not as user-facing states; CI treats any occurrence as a bug.

## Invariants

- Every check has a stable `check_id`, exactly one severity, and a numeric magnitude —
  no free-text-only results (the UI and workbook render them mechanically).
- The report always contains **all** checks with pass/fail status — a reviewer sees
  what was checked, not only what failed.
- Tolerances are named constants defined once here; no inline magic numbers in code.
- Validation never mutates data — it observes and reports (fixing a tie by plugging a
  number is forbidden).

## Error cases

- `ValidationError` (raised by ingest on any historical `fail`) — carries the full
  report.
- Checks that cannot run (e.g. H4 when retained earnings is unmapped) report status
  `skipped` with the reason — a skipped check is visible, never silently absent.

## How tested

- **Clean fixtures pass:** KO and MSFT histories produce `pass` (or
  `pass_with_warnings` listing exactly the expected warnings — assert the exact set).
- **Broken-payload fixtures fail correctly:** synthetic companyfacts variants, each
  breaking exactly one identity (assets off by 2%, CF missing FX effect, NCI
  inconsistency) → the right check fails with the right magnitude; nothing else fires.
- **Tolerance boundary tests:** residues just under / just over `max($1M, 0.1%·assets)`.
- **H2 materiality band:** a sub-materiality break yields `immaterial` with the
  quantified warning; an above-band break fails; the flows leg binds independently of
  the revenue leg (inflated-revenue synthetic); definitional and immaterial outcomes
  co-exist distinguishably in one report; GE's real filing fails on exactly FY2022.
- **Coverage gate (spec 01 step 7):** DE-shaped coverage refuses with the residual
  buckets named; NVDA-shaped coverage builds with `coverage_low`; the gate reads
  min(assets, liabilities); clean filers pass silently.
- **KHC:** H6 warns on the restated years with the recorded deltas.
- **COST:** H7 info on the 53-week year; P6 lease warning fires.
- **Skipped-check tests:** remove retained earnings → H4 reports `skipped`, not `pass`.
- **Projection tripwires:** mutation tests — deliberately mis-wire a toy engine build
  (e.g. net debt in WACC) and assert P3 catches it.
