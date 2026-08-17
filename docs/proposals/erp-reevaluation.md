# Equity risk premium — reevaluation

**Status: proposal, 2026-08-17. Nothing changed.** The owner decides.
Companion measurement: `level-decomposition.md` (ERP arms measured on the
66-name universe).

**The selection rule, stated first because it matters more than the
answer:** each candidate below is justified or rejected on published
methodology grounds only. Its effect on the gap to market is reported as a
consequence, never used as a reason. The one place this proposal found
itself near the calibration line is flagged inline in §5.

## 1. What the current 5.0% is grounded in — and the crack in it

The methodology entry already names it honestly: 5.0% is **Kroll's
recommended US ERP** (reaffirmed 2026-01-30) paired with **Damodaran's
risk-free convention** (spot 10Y) — a house combination neither source
publishes. Kroll publishes 5.0% against the *higher of a normalized 3.5% or
the spot 20Y*; Damodaran publishes his implied premium against the *spot
10Y*. The published packages are matched ERP/rf **pairs** because each side
is estimated conditional on the other.

Does the grounding still hold? The level does — Kroll has reaffirmed 5.0%
into 2026. The **pairing** is the weak point: with the spot 10Y at 4.68%,
the house combination produces a cost of equity higher than either published
package would, and it inherits neither package's internal logic. The
sharpest criticism a finance-literate reviewer can make is not "5% is wrong"
but "5% *on a spot 10Y* is a pair nobody publishes."

## 2. The candidates (published values, sources, as-of dates)

| Candidate | Value | Published basis | As-of |
|---|---|---|---|
| Damodaran implied (monthly) | **4.28%** | T12M cash flows, adjusted payout, vs spot 10Y (4.74%) | Aug 1, 2026 |
| Damodaran implied (year-start) | 4.23% | same construction | Jan 1, 2026 |
| Long-run mean of the implied series | ≈ 4.1% | 1960–2025 average of the same series | 2026 dataset |
| Kroll recommended | 5.0% | judgment-based, vs max(3.5% normalized, spot 20Y) | reaffirmed 2026-01-30 |
| Historical realized, geometric | ≈ 5.0–5.5% | stocks − 10Y T-bonds, 1928–2025 | dataset updated Jan 5, 2026 |
| Historical realized, arithmetic | ≈ 6.5–7% | same window; higher by variance drag | same |

Notes on the historical row: the published spread depends on window and
averaging (Damodaran's 2026 edition quotes 5.5–14.5% across constructions);
the last-10-year realized premium is ≈ 10%, which is exactly why trailing
windows are unusable as forward premiums — they rise *after* prices rise.
Geometric-full-window is the defensible historical representative;
arithmetic overstates a multi-year compounding premium.

## 3. The independence trade-off, argued both ways

An implied premium is solved from current index prices. Adopting it means
the model's discount rate partly inherits market pricing. This is a real
philosophical fork for a project whose stated identity is deriving
assumptions from filings rather than prices.

**For adopting an implied premium.** (a) The model already inherits market
prices on the risk-free side — the 10Y is a spot market price, and beta is
estimated from market covariances. The pure-independence position was
conceded at the WACC's foundation; the question is consistency, not
virginity. (b) The implied ERP is the *market-wide* price of risk, not the
subject company's price. Using it says: accept the market's exchange rate
between risk and return, then disagree about *this company's cash flows*.
That is what an active valuation view means operationally — the
disagreement belongs in the numerator. (c) It is the standard practitioner
choice (Damodaran's own recommendation) and updates with the rate
environment, where a flat 5% silently changes meaning as rates move.

**Against.** (a) Self-reference at the aggregate: measured against a
discount rate solved from the index's own level, the *universe* can never
read systematically mispriced — the median gap becomes a statement purely
about the model's cash-flow conservatism. The model gives up the ability to
say "the market as a whole is expensive," which some users of a DCF believe
is the point. (b) The gap this project just measured would narrow partly *by
construction*, not by insight — an uncomfortable look for a project that
refuses tuning toward prices. (c) An implied premium is a monthly-published
number: adopting it creates a staleness obligation (the existing
`damodaran_implied` preset already pins Jan-2026's 4.23% while Aug-2026
prints 4.28% — a five-month-stale "current" number is the failure mode on
display in our own repo).

Both positions are defensible. The against-case's strongest point (a) is
also answerable: single-name conclusions survive, because the implied
premium is one number for all filers — cross-sectional discrimination is
untouched (the decomposition confirms ERP moves the level ~uniformly, 61/66
names, shape unchanged).

## 4. Single constant, or a preset dimension?

The machinery for the second option already exists: the methodology-lens
presets carry provenance, rationale cards, and the what-changed diff, and
`damodaran_implied` already applies the implied package as published.

Options:

- **(a) Status quo:** default 5.0% house-mix; implied available as a lens.
- **(b) Default becomes a published package; the other packages become
  lenses.** The default must be *some* package — the argument is that it
  should be one that exists in print, with its as-of date in provenance.
- **(c) ERP as a first-class preset dimension:** every lens names its
  ERP/rf package explicitly (implied@spot-10Y / Kroll@Kroll-rf /
  historical-geometric@spot-10Y), user chooses, one stated default.

**Recommendation: (b)-shaped, with (c)'s labeling.** The defect this review
actually found is not the 5.0% level — it is the unpaired combination. The
two internally consistent candidates are the Damodaran implied package
(4.28%, spot 10Y) and the Kroll package (5.0%, normalized/20Y rf). The
engine's entire rf plumbing — market pipeline, workbook named range,
reverse-DCF bounds — is spot-10Y; adopting Kroll's rf convention would be a
larger and stranger change than adopting the implied level. On published-
methodology grounds, the internally consistent default *given the engine's
existing rf convention* is the implied package, refreshed deliberately (a
pinned, dated constant per release — never auto-fetched), with Kroll and
historical-geometric as named lenses and the flat-5% house mix retired or
kept as a labeled legacy lens.

**Calibration-line flag (the one required by the selection rule):** this
recommendation concludes at a lower cost of equity, and its measured
consequence is a +5.7pp median-gap narrowing. The argument above was built
on pairing consistency, and it would survive if the implied premium printed
*above* 5% — that is the test I applied. But the owner should apply it too:
if the pairing argument reads as a rationalization for the number that
narrows the gap, the honest alternative is (c) with the default left at the
status quo and the choice handed entirely to the user.

## 5. What each candidate does to the universe (consequences, not reasons)

From the decomposition run (n=66, valuation date 2026-08-14):

| ERP | Median gap | IQR width | Above market | Tails ±10% |
|---|---|---|---|---|
| 4.00% | −31% | 54pp | 23% | +13 / −47 |
| 4.28–4.33% (implied) | −34% | 49pp | 20% | +12 / −50 |
| 4.60% | −36% | 46pp | 18% | +11 / −51 |
| **5.00% (current)** | **−39%** | **45pp** | **17%** | **+10 / −54** |
| 5.50% | −43% | 41pp | 15% | +7 / −54 |

The dispersion and tail columns barely move: the ERP relocates the
threshold, it does not repair discrimination. Anyone hoping the ERP alone
fixes the one-sided screen will be disappointed — that finding is the
decomposition's, and it is the reason this proposal makes no claim beyond
pairing consistency.

## 6. If something lands

Whatever the owner picks: methodology entry rewritten around the chosen
package with its as-of date; the preset set relabeled so every lens names
its pair; the workbook's Methodology sheet and the `/methodology` page
inherit both automatically; known-limitations gains the staleness policy
(pinned constant, refreshed deliberately per release with a dated diff).
