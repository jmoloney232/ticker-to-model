# Spec 07 — Validation

Validation is a first-class feature (non-negotiable #3): never silently produce a model
that doesn't tie. This spec owns the check definitions, tolerances, severities, and
surfacing. Checks run in two places: the **historical gate** inside ingest (spec 01,
step 7) and the **projection invariants** inside the engine (spec 04).

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
| H2 | **Cash flow ties:** CFO + CFI + CFF + FX effect = Δcash, where Δcash uses the *same cash definition* as the mapped cash item (the 2016 restricted-cash ASU changed the total—chain-consistency matters, see spec 02 notes) | fail |
| H3 | **Net income consistency:** `NetIncomeLoss` + NCI income = `ProfitLoss` where both were reported. Honest note: `companyfacts` facts are statement-agnostic, so the classic "NI matches between IS and CF" is reframed as tag-level consistency (H3) plus the roll-forward (H4) | fail |
| H4 | **Retained-earnings roll-forward (soft):** RE_t ≈ RE_{t−1} + NI_t − dividends_t. Legitimately noisy (share retirements, some OCI reclassifications, ASU adoptions hit RE directly) → warn-only | warn |
| H5 | **Schema cross-checks** (from spec 02): reported vs derived gross profit; reported vs derived total liabilities; sum of mapped current assets vs reported total | warn |
| H6 | **Restatement delta** >1% between latest-filed and first-filed value (set in ingest, reported here) | warn |
| H7 | **53-week year** detected | info |

**Tolerances (H1, H2, H3):** `max($1M, 0.1% of total assets)`. EDGAR facts are exact
dollars, but composite/derived items (schema `derive:` rules) introduce residues;
below this threshold is mapping noise, above it is a real problem. **H4:** warn when
the residual exceeds 5% of total equity. **H5:** warn above the H1 tolerance.

A `fail` blocks the model: ingest raises `ValidationError` carrying the report — the
user sees which identity broke, by how much, in which year, with per-item provenance
(tag + accession) so the failure is diagnosable, not just loud.

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
- **KHC:** H6 warns on the restated years with the recorded deltas.
- **COST:** H7 info on the 53-week year; P6 lease warning fires.
- **Skipped-check tests:** remove retained earnings → H4 reports `skipped`, not `pass`.
- **Projection tripwires:** mutation tests — deliberately mis-wire a toy engine build
  (e.g. net debt in WACC) and assert P3 catches it.
