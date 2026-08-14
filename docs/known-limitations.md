# Known limitations

Precise edges, honestly mapped. Every entry here was found by running the pipeline
against real filings (the 29-ticker bulk scan), diagnosed to a root cause, and left
unfixed *on purpose* — because the fix needs a policy decision, a capability we've
deliberately deferred, or modeling work that belongs to a later phase. Each entry
states what breaks, why, and what fixing it would require. A tool that knows exactly
where its edges are is more credible than one that claims not to have any.

How limitations surface to the user (never as a generic error):

| Surface | Mechanism |
|---|---|
| Filer we cannot support at all | `known_unsupported.yaml` → `KnownUnsupportedError` with the reason verbatim |
| Statements that don't reconcile materially | `ValidationFailedError` carrying the full per-year report |
| Too little of the balance sheet mapped | `InsufficientCoverageError` naming the largest unattributed balances |
| Bounded gaps in an otherwise-good build | structured warnings in the assembled output (`coverage_low`, `immaterial_cash_residual`, PL checks) |

---

## 1. GE — the spin-off year's cash flow statement (FY2022)

**What breaks:** H2 (cash flow ties to the change in cash) fails for FY2022 with a
$0.37B best-basis unreconciled residual — 1.27% of as-restated revenue, above the
1%-of-revenue materiality leg. GE does not build. Every *other* GE year's residual is
individually immaterial and would be disclosed, so the materiality band has isolated
the break to exactly the spin year.

**Why:** GE spun off GE HealthCare in January 2023 (and Vernova in 2024). Its FY2022
cash flow statement presents *continuing-operations* flows, while the cash totals it
reconciles include *discontinued* operations. Our flow sum (CFO + CFI + CFF + FX)
therefore misses the discontinued-ops flow lines, and no cash-definition alternate
can bridge a gap that lives in the flows themselves. The as-restated revenue
denominator (≈$29B after two spins) also makes the same dollar residual proportionally
larger than it looked pre-restatement.

**Fixing it would require:** discontinued-operations flow composites — summing
`...ContinuingOperations` and `...DiscontinuedOperations` flow tags when a filer
presents them separately. That is a new schema *policy* (it changes what "CFO" means
for every filer that carries those tags), not a chain add, so it needs an owner
decision plus fixture tests around a spin-off filer before it ships.

## 2. XOM and NEE — extension-tag filers

**What breaks:** Exxon Mobil files its annual income statement, and NextEra Energy
its capital expenditures, only under company-specific extension tags (verified: no
standard `us-gaap` tag carries the value in any year). Revenue and capex are required
items, so neither company can build. Both are on `ingest/known_unsupported.yaml` and
get the reason shown verbatim instead of a generic failure.

**Why:** SEC rules let filers define extension taxonomies for lines they consider
company-specific. `companyfacts` exposes those tags in the company's own namespace,
which our standard-tag chains deliberately do not consume — an unreviewed extension
tag has no guaranteed semantics, and mapping it blind would violate the
no-silent-guesses rule.

**Fixing it would require:** extension-taxonomy support: consuming company-namespace
tags behind a per-filer, human-reviewed mapping (each extension tag verified against
the filed statements before it enters a chain). Deliberately a later phase — the
review burden is per-company, not per-schema.

## 3. MCD — lessee lease tagging

**What breaks:** PL7 (ROU asset and lease liability appear together) warns on
McDonald's FY2023: one side of the lessee lease balance resolves to zero. Warn-level
only — MCD builds and its statements tie.

**Why:** McDonald's is simultaneously one of the largest *lessees* (ground leases)
and *lessors* (franchisee subleases) in the market. Its lessor-side disclosures
(≈$31.5B under `LessorOperatingLeasePaymentsToBeReceived` and related tags) sit
outside our lessee-oriented lease chains, and its lessee-side tagging shifts across
years, so FY2023's lessee balance lands under a tag the chain doesn't know.

**Fixing it would require:** a dedicated lessor/lessee tag review: separate probe
tags for lessor balances (so PL7 can tell "missing lessee data" from "lessor-heavy
filer"), plus verified chain additions for MCD's lessee-side tags. Chain-add-sized
work, but it needs the review before the add.

## 4. Captive-finance balance sheets (DE; CAT partially)

**What breaks:** John Deere maps only 20% of assets and 18% of liabilities to named
line items — Deere Financial's earning assets (finance receivables, ≈$85B of
"other" noncurrent assets) and its funding debt have no slots in an industrial-company
schema. The coverage gate now *refuses* to value DE, naming those balances in the
error, instead of building a confident valuation from a fifth of the balance sheet
(which is what happened before the gate existed). Caterpillar's smaller captive arm
leaves CAT at 86% asset coverage — above the warning band, but the same shape.

**Why:** a captive finance subsidiary is a bank living inside a non-financial SIC
code. Its balance sheet (receivables portfolios, wholesale funding, securitizations)
is structurally the kind we reject when it stands alone as a company; embedded, it
evades the SIC gate and lands in residual buckets instead.

**Fixing it would require:** two things, and the second is the real blocker:
(1) schema slots for finance receivables and related funding debt (chain-sized), and
(2) an engine policy for valuing a blended industrial + spread-lending business —
a DCF on unlevered FCF is the wrong machine for the finance arm (interest is its
*revenue*, debt is its *raw material*). That is a valuation-model decision
(segment-style split? blended with disclosure?), not a tag problem, and is out of
scope until segments are.
