# Level decomposition — where the median gap lives

**Status: measurement record, 2026-08-17. Proposes nothing.** The owner
decides any convention change after reading this. Reproduce with
`python -m diagnostics --levels` (offline against `.scan_cache.sqlite`,
valuation date 2026-08-14).

## Framing

The bias rounds measured the *slope* — whether the gap to market is
predictable from company characteristics — and reduced it. This measures the
*level*: the median filer reads ~40% below market and most filers read below.
A model that reads below market on nearly every name gives a ranking but no
threshold — it can say which company is cheapest, but not whether anything is
cheap. The purpose here is locating which global constants carry that level,
individually and in combination. Restoring discriminating power is the goal,
not landing nearer the market: every arm below is a *hypothetical override
run*, chosen from published methodology alternatives, and nothing changes
without owner sign-off.

**Universe:** all 68 cached builders; 66 valued (BA and SNOW report Gordon
honestly unavailable). Baseline includes the same-day cap-separation fix
(ORCL/LLY/TXN reclassified), so nothing here double-counts it.

**Discriminating-power metric** (added to the diagnostic, reported per arm):
share of the universe above vs below market, IQR of gaps, and whether both
tails hold names beyond ±10%. A screen with one empty tail isn't a screen.

## The arms

Each single arm replaces one global constant with a published alternative,
everything else held:

| Arm | Replaces | With |
|---|---|---|
| `erp_400/433/460/550` | ERP 5.0% flat | 4.0% / 4.33% (Damodaran implied, Jan-2026) / 4.6% / 5.5% |
| `g_ceiling_rf` | 2.5% terminal-g house cap (mature filers only) | the published g ≤ 10Y ceiling (4.68%) |
| `sbc_addback` | SBC expensed | street add-back |
| `tax_21` | 25% terminal marginal rate | 21% federal statutory |
| `no_reinv_haircut` | terminal RR = g/ROIC_t | ROIC_t = 200% (RR ≈ 0, naive Gordon) |
| `capex_fade_all` | capex %rev held flat (non-modifier filers) | fade-to-maintenance for everyone |

Compounders already default g = rf, and a declining filer's g is anchored to
its own trajectory deliberately — the g arm lifts neither (it isolates the
house cap, which binds only on mature filers: 40 of 66 move).

## Results

| Arm | Median gap | IQR | Width | Above | Tails ±10% |
|---|---|---|---|---|---|
| **base (shipped)** | **−39%** | [−64%, −19%] | 45pp | 11/66 (17%) | +10 / −54 |
| erp_400 | −31% | [−58%, −3%] | 54pp | 23% | +13 / −47 |
| erp_433 | −34% | [−60%, −11%] | 49pp | 20% | +12 / −50 |
| erp_460 | −36% | [−61%, −15%] | 46pp | 18% | +11 / −51 |
| erp_550 | −43% | [−66%, −25%] | 41pp | 15% | +7 / −54 |
| g_ceiling_rf | −24% | [−53%, +16%] | 69pp | 30% | +17 / −42 |
| sbc_addback | −35% | [−61%, −16%] | 45pp | 18% | +10 / −54 |
| tax_21 | −37% | [−62%, −16%] | 46pp | 18% | +11 / −52 |
| no_reinv_haircut | −32% | [−59%, −10%] | 49pp | 21% | +10 / −50 |
| capex_fade_all | −39% | [−64%, −19%] | 45pp | 17% | +10 / −54 |
| erp_g_pair | −17% | [−45%, +31%] | 76pp | 39% | +22 / −36 |
| stack (all six) | +19% | [−28%, +72%] | 100pp | 59% | +35 / −25 |

Paired per-filer median deltas (the cleaner "contribution" statistic):
ERP@4.33 **+5.7pp** (61/66 names move), g ceiling **+5.6pp** (40/66),
reinvestment haircut **+4.6pp** (56/66), tax **+2.8pp** (60/66), SBC
**+1.2pp** (39/66 — but +8 to +29pp on the SBC-heavy: INTU +29, NOW +24,
ADBE +22, META +18), capex normalization **+0.0pp** (5/66).

## Findings

**1. The level lives in the terminal block.** The three largest constants —
the g cap, the reinvestment haircut, and the ERP — all act on the perpetuity
denominator or the flows it capitalizes. Together (with tax) they account for
essentially the whole median gap; SBC is a cohort-specific add; capex
normalization contributes nothing.

**2. The g cap is the largest single constant and the only one that restores
shape.** Lifting it moves the median +15pp (distribution) but, more
importantly, widens the IQR 45pp → 69pp and repopulates the upper tail
(+10 → +17 names). Mechanism: it binds *heterogeneously* — only mature
filers, and hardest on low-WACC ones (HON +91pp, SYY +88pp, VZ +87pp,
YUM +72pp). Every other single arm shifts the level while preserving the
shape, because a flat constant applied to every filer cannot change a
ranking.

**3. ERP is a pure level dial.** It moves 61/66 names nearly uniformly:
median −31% at 4.0%, −34% at 4.33%, −39% at 5.0%, −43% at 5.5%, with the IQR
width and above-share barely responding. Choosing an ERP sets *where the
threshold sits*, not *which names clear it*. (Full treatment in the ERP
proposal, `erp-reevaluation.md`.)

**4. The constants compound — super-additively.** On filers where every arm
is valued, the median sum of the six individual deltas is **+31pp**; the
median stack delta is **+51pp** — an interaction term of roughly +20pp. The
mechanism is 1/(w_T − g): the ERP lowers the denominator's left side while
the g cap raises its right side, and the reinvestment haircut multiplies the
flows the shrunken denominator capitalizes. The ERP × g pair alone is
+17.8pp against +11.3pp for its parts (per-filer interaction median +2.2pp,
far larger on low-WACC names). Consequence: these constants cannot be
re-decided one at a time and summed — any package must be measured as a
package.

**5. The stack shows why the conservative set exists.** Under all six
alternatives at once, low-WACC perpetuity-sensitive names explode: VZ +44% →
+539%, DUK −110% → +258%, HON +43% → +265%, SYY +14% → +219%. The
denominator w_T − g compresses toward zero exactly where WACC is lowest, and
RR ≈ 0 hands the perpetuity undiluted flows. A level correction that simply
adopts the street's generous end trades a one-tailed screen reading low for
a one-tailed screen reading high on utilities and staples.

**6. Capex normalization's contribution has already been delivered.** Only
5/66 names move under `capex_fade_all`, median +0.0pp — after the classifier
rounds (including today's cap separation), the reinvestment-heavy modifier
already fades the filers where held-flat capex was distorting the terminal.

**7. EPV's maintenance-capex constant is a serial-acquirer story, not a
level story.** Maintenance = depreciation-only moves the EPV median just
+0.5pp — but +40pp of price for BMY, +25pp VZ, +24pp ABBV, +16pp IBM
(amortization-heavy filers whose acquired-intangible amortization is not a
cash reinvestment need). The EPV base median (−63%) is itself not a defect:
a no-growth floor *should* read below market for growers.

**8. Expensed R&D is not worth a build for the level question.** Rough 5y
straight-line capitalization across the 24-name R&D-heavy cohort (R&D ≥ 5%
of revenue): the EPV-face uplift is ≤ +5% of price for 22 of 24 names
(META +9% at 29% R&D intensity; F's +20% is a low-price artifact). The
DCF face is a small *negative* tax-timing flow (−t × (R&D − amortization)):
capitalization is reclassification, not value creation, in an FCFF model.
Verdict: skip the build for level purposes; note it as a possible future
EPV refinement for META-class R&D growers.

## Reading (analysis, not recommendation)

The median gap decomposes, in round numbers, as: terminal-g cap ~6pp
(median; far more where it binds), ERP-at-implied ~6pp, reinvestment haircut
~5pp, terminal tax ~3pp, SBC ~1pp (much more on the SBC-heavy cohort), plus
an interaction term that grows with how many are moved together. No single
constant is a smoking gun; the level is the *sum of deliberately
conservative defaults*, each individually defensible, all leaning the same
way, compounding through the terminal denominator.

Two structural observations for whatever the owner decides:

- **Only heterogeneously-binding changes restore discrimination.** The g cap
  is the one constant whose removal changes the shape of the distribution.
  Flat constants (ERP, tax) move the threshold, which is a legitimate
  decision, but cannot repopulate a tail by themselves.
- **The screen's upper tail is thin but not empty** — ADBE +95%, XEL +67%,
  BKNG +65%, VZ +44%, HON +43%, INTU +24%, NKE +23%, META +18%, SYY +14%,
  DAL +12%, CMG +6% read above market today. Whatever package lands should
  be judged by whether *both* tails carry conviction, not by the median
  alone.
