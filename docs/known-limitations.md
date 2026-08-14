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

## 5. Linear growth fade — the cumulative path, not the first year

**What breaks:** nothing mechanically — this is a modeling limitation, documented
because we know the better answer and scoped it out deliberately. The FY1 revenue
growth default is capped at 30% (uncapped CAGR displayed alongside), which fixes the
*starting point* for hypergrowth filers. But the deeper issue is the **cumulative
five-year path**: a linear fade from a high starting rate spends most of the window
at elevated growth. A filer entering at the 30% cap fading linearly to 2.5% still
compounds to roughly +90% total revenue by FY5 — the fade's *shape*, not its
endpoints, drives the number, and a linear shape is generous to fast growers.

**Why:** linearity was chosen for v1 because it is the simplest fade a reviewer can
verify by eye in the workbook, and every parameter of it is visible (start, end,
five steps). The generosity is a property of the shape, not a bug in the
implementation.

**Fixing it would require:** a curved, front-loaded fade — decay most of the excess
growth in FY1–FY2 (exponential decay toward terminal g, or a half-life
parameterization) so cumulative revenue tracks how hypergrowth actually decays.
Deferred to v1.1: it adds a shape parameter that needs its own derivation rule and
its own defense, and v1's cap-plus-displayed-CAGR keeps the default honest in the
meantime. The FY1 cap and the `growth_fade_steep` warning are the v1 mitigations.

## 6. Operating leases held flat in projections (v1.1 candidate)

**What breaks:** nothing mechanically — projected lease ROU and lease liability are
held at the latest historical value, a disclosed modeling choice (spec 04). For
lease-heavy filers (DAL, MCD, SBUX — the same names the `lease_heavy` warning
exists for) this means the lease footprint doesn't grow with revenue: a business
plan that implies opening stores or adding aircraft carries a fixed lease base.

**Why:** v1 has no defensible history-derived rule for lease growth that doesn't
also require modeling lease *turnover* (renewals at current rates vs. embedded
rates), and a flat, labeled line is more honest than a fabricated growth rule.

**Fixing it would require:** projecting lease balances as a percentage of revenue
(3y average, consistent with the other operating items) and flowing the implied
lease additions through the cash flow statement. Logged as the **first v1.1
candidate** — it is chain-of-consequence work (BS, CF, and the lease_heavy
disclosure all move together), not a one-line change.

## 7. Cost-line tag chains for MCD-class and AMZN-class filers (queued chain round)

**What breaks:** nothing anymore — but for four filers a material share of
operating costs lives in tags the schema doesn't map (MCD 43.5% of revenue:
company-operated restaurant expenses and franchise occupancy costs; AMZN 37.8%:
fulfillment / technology-and-content / marketing; ABBV 21.6%; CAT 6.3%). Since
the 2026-08-14 fix these costs are projected via the explicit
`unclassified_costs_pct` closure line (margins correct by identity) and surfaced
by the `unclassified_costs` warning and the expense-coverage metric — honest,
but the projected line is *unnamed*, so the workbook's income statement will
show "unclassified costs" instead of the filer's real cost categories.

**Why:** these filers use industry- or company-specific us-gaap tags
(`FoodAndPackagingExpense`, `FulfillmentExpense`-class tags) that no fallback
chain currently covers; guessing them into SG&A would be a wrong label where
"unclassified" is a true one.

**Fixing it would require:** the standard chain round — per-filer verification
of each candidate tag against the filed statements (owner process: chain adds
are verified per filer, never added speculatively), then re-running the scan
and the diagnostic batch. **Queued as the next ingest chain round** (owner
decision, 2026-08-14). The expense-coverage metric now reads E21% for MCD, so
the fixed alarm also measures this queue's progress.

## 8. Synthetic ratings where actual agency ratings exist (v2)

**What:** the cost of debt is estimated by synthetic rating (coverage → spread)
for every filer. Synthetic rating is a fallback method intended for *unrated*
issuers — most large-caps in this universe carry an actual Moody's/S&P rating
that would price their debt directly. The kd_synthetic derivation string and
the methodology entry disclose this.

**Why:** agency ratings aren't in EDGAR company facts; ingesting them means a
new data source (or parsing ratings out of filings' exhibits, which is
unreliable).

**Fixing it would require:** an agency-ratings ingest source with its own
staleness/provenance treatment, a documented mapping from rating to spread
(same published table), and precedence rules (actual rating wins; synthetic
stays as the fallback and the cross-check). Tractable v2 item.
