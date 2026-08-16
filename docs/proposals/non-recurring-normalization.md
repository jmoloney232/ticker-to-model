# Proposal: non-recurring items in normalization — scope, rule, cost

*Status: PROPOSAL — no code. Owner-requested design decision (2026-08-16)
before any implementation. This is the project's largest remaining known
limitation (known-limitations §one-off years; surfaced by KHC's impairment
year and MRK's acquired-IPR&D charges, and partially mitigated for the
declining profile by the window-median rule, owner-approved 2026-08-16).*

## The problem, precisely

Derived defaults normalize history — margins, tax rates, ROIC, the EPV
margin — and one-off years contaminate the normalization. Today's mitigations
are structural (medians for the declining profile, full-window means for
cyclicals, loss-year exclusions in the tax derivation), not semantic: nothing
in the engine knows *that* FY2019 contained a goodwill impairment. The cost
showed up measurably in the external comparison: our MRK EPV ran ~47% below a
competitor that evidently scrubs charge years.

The scope of this proposal is **normalization inputs only** — derived
defaults. Reported history stays as filed, always; nothing here touches the
statements a reviewer sees, validation, or WACC.

## What XBRL can and cannot detect

**Detectable — filed under named us-gaap tags, mappable by ordinary chains:**

| Charge class | Representative tags | Reliability |
|---|---|---|
| Goodwill impairment | `GoodwillImpairmentLoss` | high — near-universally tagged |
| Asset impairment | `AssetImpairmentCharges`, `ImpairmentOfLongLivedAssetsHeldForUse`, `ImpairmentOfIntangibleAssetsExcludingGoodwill` | high, some overlap between tags |
| Restructuring | `RestructuringCharges`, `RestructuringSettlementAndImpairmentProvisions` | high for the charge; severance vs. exit-cost split varies |
| Acquired IPR&D | `ResearchAndDevelopmentInProcess`, `BusinessCombinationInProcessResearchAndDevelopmentExpensed` (MRK's case) | medium — pharma tags this inconsistently across vintages |
| Litigation settlements | `LitigationSettlementExpense`, `LossContingencyAccrualProvision` | medium |
| Gains/losses on disposal | `GainLossOnDispositionOfBusiness`, `GainLossOnSaleOfPropertyPlantEquipment` | high |

**Not detectable from companyfacts:**

- One-offs embedded untagged inside COGS/SG&A (inventory write-downs folded
  into cost of revenue, legal costs inside SG&A) — the majority of small
  one-offs.
- Anything tagged only under company extension taxonomies (the XOM class).
- "Non-recurring" as *management* defines it (non-GAAP adjustments) — by
  design; we would not want it anyway.
- The recurrence question itself: XBRL says a restructuring charge exists,
  never whether the company restructures every single year.

## The proposed rule (if built)

1. **Ingest** ~8 optional charge items (the table above) as ordinary schema
   chains — `missing_rule: omit`, never zero-logged, full provenance.
2. **Compute a per-year `one_off_charges` memo line** = sum of detected
   charge items, with per-item detail. Purely informational at this stage;
   surfaced in the Audit tab and the workbook's historical sheet as a memo.
3. **Normalization uses charge-adjusted operating income** (`EBIT +
   one_off_charges`, sign-aware for gains) for derived DEFAULTS only:
   margin ratios, `epv_margin`, terminal ROIC, the profile classifier's
   margin-range measure. Every adjusted default's derivation string names
   the years and magnitudes scrubbed.
4. **Recurrence guard — the rule that keeps the rule honest:** if a charge
   class appears in ≥ 3 of the observed years, it is treated as RECURRING
   for that filer and not scrubbed (a serial restructurer's restructuring is
   an operating cost; a serial acquirer's IPR&D is its R&D). This guard is
   the difference between normalization and flattery.
5. **Materiality floor:** scrub only years where detected charges exceed 1%
   of revenue (the H2 materiality leg — same published constant), so the
   memo line doesn't churn defaults over noise.
6. **Disclosure:** a `one_off_scrubbed` warning per affected year;
   methodology entry; the un-scrubbed default shown alongside (the
   `revenue_cagr_uncapped` display pattern).

## What it would cost

- **Schema:** ~8 optional items + chains, each needing verification against
  2–3 real filers (KHC goodwill, MRK IPR&D, MMM litigation, GE
  restructuring as the recurrence-guard case). One chain round.
- **Engine:** an adjusted-EBIT series threaded through `derive_assumptions`
  and `_profile_measures` — touches the margin, ROIC, and classifier
  paths; each needs its adjusted/unadjusted pair tested. The classifier
  thresholds were set on unadjusted history — a re-scan of the 27-filer
  distribution is required to confirm the thresholds still sit in the same
  separation gaps (NOT to re-tune them).
- **Surfaces:** methodology entry, workbook memo rows, Audit tab, this
  document folded into known-limitations. Golden moves (KHC, MRK class);
  diff review per protocol.
- **Estimate:** comparable to the profile-classifier round in scope. The
  recurrence guard and the classifier re-scan are the two places where
  judgment (and owner review) concentrates.
- **Risk:** partial detection creates asymmetry — filers who tag charges get
  cleaner normalization than filers who bury them, and the difference is
  invisible. Mitigated by the memo line being visible evidence of what was
  and wasn't found, but not eliminated.

## Alternatives considered

- **Robust statistics everywhere** (medians for all profiles, not just
  declining): cheap, no semantics, but a median can't help 3-year windows
  with one distorted year sitting at the median position, and it leaves the
  charge invisible rather than disclosed. Complementary, not sufficient.
- **Winsorizing margins** at window percentiles: a tuning surface with no
  economic meaning per filer; rejected.
- **Do nothing:** the honest default today. The external comparison priced
  this choice at roughly −47% on MRK's EPV vs. a scrubbing competitor.

## Recommendation

Build it as scoped, in a dedicated round, **after** the current audit items
settle — the recurrence guard (rule 4) and the classifier re-scan are the
two pieces that need owner sign-off at design time, and both are stated
above ready for that review. Until then the window-median rule covers the
declining profile, and the limitation stays documented.
