# Proposal: a minimum terminal spread (WACC − g floor)

*Status: PROPOSAL — no code. Owner-requested (2026-08-16) after the
compounder decomposition audit flagged the 1/(WACC − g) sensitivity.
Measurements below are read-only runs at the 2026-08-14 valuation date,
post the split-D&A-basis fix and the fade removal. Nothing here is tuned
toward market prices or analyst consensus.*

## The economic argument, stated

A perpetuity's value is `FCF_t / (WACC − g)`. As the spread approaches
zero the implied terminal multiple `1/(WACC − g)` diverges — a business
whose perpetual growth approaches its cost of capital has unbounded value,
which is not a property any real company has. A minimum spread is therefore
a statement about what is economically representable, not a calibration.

Two non-market anchors put a number on "minimum":

1. **The model's own noise floor.** WACC is estimated: the beta regression's
   standard error on ~104 weekly observations puts ±0.75–1.25% on the cost
   of equity alone, and the ERP is contested to ±50bp. Terminal g is
   estimated no tighter than ±50bp. A spread below ~2% is *inside the joint
   uncertainty of its own inputs* — the output's leading digit flips within
   input noise, so the printed value is not an estimate, it is an artifact.
2. **Implied duration.** `1/(WACC − g)` is also the order of the value's
   duration in years: a 1% spread asserts century-scale confidence in a
   competitive advantage; 2% asserts ~50 years. The engine already refuses
   comparable assertions elsewhere (negative-anchor refusals, the 30%
   growth cap) on "no one can stand behind this" grounds.

## What the measurements actually show

### (c) Affected filers: none, at any candidate floor up to 2.5%

Every compounder, current defaults (g = 10Y = 4.63%), with gordon values
under floors of 1.5%, 2.0%, 2.5% (`g′ = min(g, WACC − s)`):

| Ticker | WACC | Spread today | Gordon | Floor 1.5% | Floor 2.0% | Floor 2.5% | TV share of EV |
|---|---|---|---|---|---|---|---|
| COST | 7.47% | **2.84%** | 663.12 | unchanged | unchanged | unchanged | 77% |
| INTU | 7.81% | 3.18% | 503.45 | unchanged | unchanged | unchanged | 66% |
| ADBE | 9.05% | 4.42% | 500.55 | unchanged | unchanged | unchanged | 63% |
| CMG | 9.25% | 4.62% | 36.65 | unchanged | unchanged | unchanged | 67% |
| NFLX | 9.27% | 4.64% | 64.09 | unchanged | unchanged | unchanged | 60% |
| MSFT | 9.93% | 5.30% | 477.29 | unchanged | unchanged | unchanged | 64% |
| GOOGL–AVGO (6 more) | 10.2–14.1% | 5.6–9.5% | — | unchanged | unchanged | unchanged | 37–61% |

The structural reason: with g capped at the risk-free rate and one WACC,
the spread is bounded below by roughly `equity-weight × β × ERP` plus debt
terms. The g-at-rf rule **cannot** push the spread toward zero unless beta
itself collapses — the tightest case in the universe is COST (β ≈ 0.66)
at 2.84%. A floor at 2% is a guard rail that binds nobody today; a floor
at 3% would start silently re-writing an owner-approved default for the
lowest-beta name with no evidence of misbehavior, which is exactly the
kind of quiet re-tuning this project refuses.

### (e) The rate-sensitivity question: the violent swing does not exist

Measured, rf ± 100bp with g tracking rf (today's rule) and WACC re-derived:

| Ticker | rf 3.63% | rf 4.63% (base) | rf 5.63% | Spread across the range |
|---|---|---|---|---|
| COST | 691.60 (+4%) | 663.12 | 635.75 (−4%) | 2.85% → 2.84% → 2.84% |
| ADBE | 517.51 (+3%) | 500.55 | 484.22 (−3%) | 4.43% → 4.42% → 4.40% |
| MSFT | 499.07 (+5%) | 477.29 | 456.56 (−4%) | 5.30% → 5.30% → 5.29% |

The g-at-rf rule is **self-hedged against parallel rate moves**: rf passes
through the cost of equity one-for-one and through g one-for-one, so the
spread — and with it the terminal multiple — is nearly invariant. ±100bp
in the 10Y moves these valuations ±3–5%, and a 2% floor changes none of
those cells. The violent 1/(WACC − g) lever the decomposition measured
(COST +$241/share) is **cross-sectional** — the one-time gap between the
2.5% house cap and the rf ceiling — a disclosed profile lever, not a rate
instability. The floor neither fixes nor bounds a swing, because there is
no swing to fix; the answer to "does the floor fix it?" is that the
premise fails, measurably, and this document records the measurement.

### Where the real exposure is

1. **User overrides.** Today only `g ≥ WACC` blocks. A user can set
   g = WACC − 10bp and the engine prints a 1000× terminal multiple with no
   comment. This is the actual hole.
2. **Future low-beta outliers.** A β ≈ 0.4 filer classified compounder
   would land near a 2% spread from defaults alone. None exists in the
   current universe; one could enter it.

## Proposed rule (if built)

1. **Floor the derived default:** `terminal_growth ≤ WACC − 2.0%` at
   derivation time. A no-op for every current filer; if it ever binds, the
   derivation string says so and a `terminal_spread_floor` warning fires.
2. **Warn on overrides inside the band:** g in `(WACC − 2%, WACC)` →
   soft warning naming the implied terminal multiple and the noise-floor
   argument ("the spread is inside the estimate's own uncertainty").
   The existing hard block at `g ≥ WACC` stays the hard boundary —
   (b) the floor nests strictly inside it and cannot conflict with it.
3. **Overrides are warned, never clamped** — (d) the derived default is
   ours to bound; an override is the user's model, and the project's
   existing pattern (terminal-g override warns, g ≥ WACC blocks) extends
   unchanged. The reverse DCF is untouched: an *implied* g inside the band
   is information, not an assertion.
4. **Sensitivity grids unchanged:** cells with g ≥ WACC already print "—";
   cells inside the band keep printing (the grid exists to explore).
5. Constant `2.0%` lives in methodology.yaml with the two anchors above as
   its derivation; parity-tested like the other published constants.

## Alternative considered: terminal beta convergence

Damodaran's other stable-growth constraint — terminal-period beta moves
toward 1 (he brackets 0.8–1.2) — would widen COST-class spreads by raising
terminal WACC rather than lowering g, and it is the more economically
expressive fix (no company stays lower-risk than the market forever while
growing with the economy). It requires a two-WACC engine (explicit-period
vs terminal), which touches the workbook's discounting structure and the
sensitivity grids — a structural round, not a guard rail. Recorded here as
the v2-grade path; the spread floor does not preclude it.

## Recommendation

Build the guard-rail version (rule above): it closes the override hole,
future-proofs against low-beta entrants, changes no current valuation by
construction, and costs ~a day including tests. Decline the 3% variant
(would rewrite COST's approved default without evidence). Treat terminal
beta convergence as the eventual structural answer if utilities or other
low-beta sectors ever enter scope.
