# Proposal: terminal beta convergence (time-varying cost of capital)

*Status: PROPOSAL — no code. Owner-commissioned (2026-08-17) after the
two-arm bias re-measurement showed beta correlation unmoved at −0.39 across
the universe and both WACC and beta correlations* worsening *inside the
compounder cohort. Measurements below are exact re-discountings of each
filer's existing UFCF flows and terminal anchor under the proposed rate
path (flows are WACC-independent, so the simulation is not an
approximation), at the 2026-08-14 valuation date, post Part 1. Nothing is
tuned toward market prices; the success criterion was stated in advance.*

## The economics

A two-year weekly regression beta is an estimate of *present* conditions —
current leverage, current product cycle, current index composition — with
known mean-reverting measurement error. A firm modeled in perpetuity is a
diversified claim on the economy: holding its estimated beta fixed for
years 1 through ∞ asserts that today's risk snapshot is a permanent
property. The stable-period convention (Damodaran: stable-growth betas
move toward 1, bracketed 0.8–1.2) converges risk toward the market as the
forecast matures. Blume's own adjustment — already applied — is the
one-period version of the same empirical fact.

## 1. Construction

- **Current beta β₀** — unchanged: 2y weekly OLS vs SPY, Blume-adjusted
  (owner re-confirmed 2026-08-13). See §6.
- **Terminal beta β_T = midpoint(β₀, 1.0)** — half the deviation from
  market risk persists in perpetuity. Argued below; editable as a new
  `terminal_beta` field (a full-convergence view is one edit: 1.0).
- **Path**: β fades **linearly** from β₀ in year 1 to β_T in the final
  explicit year, exactly like the tax and growth fades — the engine just
  removed a curve that earned nothing; no new shapes.
- **Per-year rate**: WACC_i = wE·(rf + β_i·ERP) + wD·Kd_after-tax, with
  **weights, Kd, rf, ERP held at current values** — only the equity-risk
  component converges. Weights held to avoid circularity; synthetic Kd is
  already a forward-looking marginal rate. Disclosed.
- **Discounting**: cumulative factor df_i = df_{i−1} · (1+WACC_i)^−Δt_i
  (Δt from the existing midyear/stub exponents — timing conventions
  untouched); beyond year N the terminal rate w_T = WACC at β_T applies.

**Why midpoint, not full 1.0:** Blume's regressions show *partial*
convergence; and our own measurement (below) shows full convergence
pushing the defensive-staples cohort 30–40% below market (PG −9% → −42%,
PEP +4% → −39%, CL −1% → −40%, KO −51% → −71%) — asserting that
half-century-observed low systematic risk vanishes entirely is a stronger
claim than the evidence supports. Midpoint also matches the engine's
existing terminal_roic_fade precedent (half the excess persists) and adds
**no free parameter**. A clamp band [0.8, 1.2] was rejected: two new
constants where midpoint needs none. **Why not keep flat beta:** that is
the measured defect this proposal exists to fix.

## 2. Which discount rate each method uses

| Surface | Rate | Why |
|---|---|---|
| Explicit years | per-year path WACC_i via cumulative df | risk converges as the modeled firm matures; the per-year rate row is visible, auditable |
| Gordon TV | **w_T** in the denominator; discounted back at the path df | the perpetuity lives entirely in the stable period |
| Exit-multiple TV | no rate in the anchor; discounted at the path df | a sale price is a point-in-time event; only the discounting changes |
| Implied-g crosscheck | against w_T | consistency with the perpetuity it interrogates |
| **EPV** | **inherits the full two-phase structure**: flat earnings, path discounting over the explicit window, capitalized at w_T beyond | see below — this is a design decision, not a detail |
| Reverse DCF | inherits automatically | solves through the same machinery |
| WACC × g grid | row offsets shift the **entire path and w_T in parallel**; g columns re-project as today; center cell still equals the base case (tested invariant) | a parallel shift is the only construction that keeps the grid interpretable |

**The EPV argument.** EPV is a perpetuity, so its rate treatment is the
sharpest version of the question. Keeping EPV at the current single WACC
would (a) break the owner-loved property test that a zero-growth DCF
converges to EPV at rel 1e-9 — the two sides would discount identical
flows at different rates — and (b) make "value of growth = Gordon − EPV"
a comparison across two different rate structures, i.e. meaningless. And
the economics point the same way: the regression beta is an estimate of
present conditions for EPV too; a no-growth firm's measured risk is not
more permanent than a growing firm's. So EPV becomes: flat earnings power,
discounted along the same path, capitalized at w_T — the property test is
preserved **by construction**, and the workbook EPV block stays closed-form
(an annuity at the path plus a discounted perpetuity).

## 3. Interaction with the g-at-rf lever — prediction, then the check

**Predicted in advance** (recorded in the simulation header before it ran):
the measured failure mode is that terminal payoff scales as 1/(WACC − g)
with one WACC, so beta convergence — which compresses cross-sectional
terminal-rate dispersion by half — should compress both tails: high-WACC
compounders (AVGO, NOW, NVDA) rise as w_T falls toward market; low-WACC
above-market names (ADBE, BKNG, INTU) fall as w_T rises; both correlations
move toward zero, universe and cohort.

**The check — mostly right, two honest misses:**

| Ticker | β | WACC | Gap today | Midpoint | Full 1.0 |
|---|---|---|---|---|---|
| AVGO | 1.96 | 14.1% | −77% | −72% | −63% |
| NVDA | 1.71 | 13.2% | −37% | **−24%** | −2% |
| NOW | 1.28 | 11.0% | −55% | −52% | −48% |
| INTU | 0.69 | 7.8% | +46% | **+24%** | +9% |
| ADBE | 0.95 | 9.05% | +90% | +85% | +81% |
| BKNG | 1.05 | 9.2% | +62% | +65% | +68% |

The high-WACC tail compresses exactly as predicted, and INTU confirms the
low-beta side. The misses: **ADBE barely moves** — its β is 0.95, already
at market, so its +90% is growth-history-driven, not rate-driven, and no
cost-of-capital rule should move it (the external-comparison decomposition
already documented that gap as the model believing ADBE's filed history);
and **BKNG rises slightly** — its β is 1.05, marginally *above* market, so
I had misattributed its premium to a low WACC. Convergence fixes
rate-shaped errors and correctly leaves these two alone. Also notable:
VZ's +91% outlier — the most extreme in the universe — collapses to +44%
(midpoint), because a β of 0.45 priced telecom cash flows at 5.6% forever.

## 4. Projected effect on the success criterion

Exact re-discounting of all 46 builders (characteristics held at current
WACC/beta for comparability with the 2026-08-16 panels):

| Panel | corr(WACC) | corr(beta) | corr(growth) | median gap |
|---|---|---|---|---|
| Universe, today | −0.501 | −0.400 | +0.092 | −35% |
| Universe, **midpoint** | **−0.326** | **−0.215** | +0.182 | −40% |
| Universe, full 1.0 | −0.144 | −0.030 | +0.275 | −43% |
| Compounders, today | −0.581 | −0.533 | −0.264 | −18% |
| Compounders, **midpoint** | **−0.419** | **−0.367** | −0.229 | −19% |
| Compounders, full | −0.213 | −0.161 | −0.155 | −20% |

Midpoint convergence cuts both target correlations by roughly a third in
both populations; full convergence nearly zeroes them across the universe
— but mechanically (the terminal rate stops depending on beta at all) and
at the staples cost above, while slightly *worsening* the growth
correlation. The median gap drifts more negative under both variants —
per the stated rules, that is context, not a success or failure. The
**binding** measurement is the post-implementation two-arm re-run; if it
disagrees with this projection, the projection loses and gets reported.

## 5. Workbook consequences

Three new live rows on the Valuation sheet: the beta path (linear-fade
formula off `beta` and `terminal_beta` named ranges), the per-year WACC
(closed form off the path), and the cumulative discount factor
(df_i = df_{i−1}·(1+w_i)^−Δt_i, stub exponent preserved in year 1) — the
UFCF PV row switches from POWER(1+wacc,−t) to the df row. Terminal cells
take w_T (its own named range/cell). The EPV block becomes annuity-plus-
discounted-perpetuity (still closed-form). The sensitivity grids' row
offsets add to the path cells (parallel shift). No circular references —
the path is strictly feed-forward. Gate: the LibreOffice round-trip must
diff every one of these cells against the engine, and the grid-center
invariant must hold. This is the largest workbook change since phase 3 and
the effort estimate reflects it.

## 6. The derived beta itself

Keep 2y weekly, Blume-adjusted, for β₀ (owner re-confirmed 2026-08-13;
citable as Bloomberg's default). The composition is stated openly: Blume
shrinks estimation error and predicts next-period beta (⅓ toward 1);
midpoint convergence composes to β_T = ⅓β_raw + ⅔ — a partial double-count
of the same mean-reversion phenomenon, bounded and disclosed, addressing
two different things (measurement error now; economic maturation in
perpetuity). The alternative — raw β₀ with full convergence absorbing
Blume's role — was rejected because explicit-period rates would then carry
unshrunken regression noise. A side benefit worth naming: convergence
makes the valuation *less* sensitive to the estimation window (the
terminal rate is dominated by β_T), which softens the standing
short-window critique without relitigating it.

Interaction with Part 1's spread floor: the floor and the g ≥ WACC block
both move to **w_T** (the perpetuity's denominator). For low-beta names
w_T rises, so the floor becomes even less binding; the tightest projected
spread in the universe stays above 3%.

## Implementation and verification (if approved)

Engine (wacc path builder, schedule discounting, both terminal legs, EPV
two-phase, reverse, grids), workbook (rows above), tests: the **reduction
invariant** — `terminal_beta = beta_used` must reproduce today's model
bit-identically (the new machinery nests the old exactly); the g=0
DCF→EPV convergence property preserved; grid-center-equals-base; golden
refreeze with per-key diff review (values move per §3–4); LibreOffice
round-trip; full rescans; then the two-arm bias diagnostic re-run —
**success = beta and WACC correlations toward zero in both populations,
reported plainly either way.** Scope: comparable to the EPV round;
methodology entries (terminal_beta, discount-rate structure),
known-limitations update, and the frontend picks the new field up through
the registry with no code.
