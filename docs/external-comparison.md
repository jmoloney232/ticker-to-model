# External comparison — fresh tickers vs. popular online models

*Run 2026-08-16 against the 2026-08-14 close. Valuation date 2026-08-14 on both
sides; ValueInvesting.io's page prices matched our valuation-date prices to the
cent on every ticker, so the gaps are directly comparable.*

Thirteen tickers were chosen from outside every set this project had ever
touched — no fixtures, no diagnostic-universe names, nothing the tag chains
were hardened against. Nine built; four were refused (see
[the refusals](#the-refusals-and-the-build-rate)). For the nine, both of our
models (DCF and Earnings Power Value) are compared against the same two models
published by ValueInvesting.io, with analyst consensus price targets as a
market-side reference point.

## Sourcing limitation — read this first

This comparison rests on **one full-methodology competitor**. Alpha Spread,
GuruFocus, and Simply Wall St all block automated access (HTTP 403), so their
values could not be collected systematically; the only Simply Wall St figure
cited below comes from a search snippet and is dated. ValueInvesting.io was
fully retrievable for every ticker, and analyst consensus targets
(stockanalysis.com) provide an independent market anchor — but a reader should
weigh the sample size accordingly: this is a two-source comparison, not a
survey of the field.

Analyst price targets are a third data point, **not ground truth and not a
calibration target**. Nothing in this project's defaults is tuned toward them,
or toward market prices, as a matter of policy.

A second caveat: ValueInvesting.io's per-ticker worksheets are paywalled. Where
this document explains *why* our number and theirs diverge, the explanation is
**inferred from the direction and size of the gap plus their published
methodology family** — it is reasoned, not read from their inputs. Every such
explanation below is labeled **Inference**.

## The Intel panel

Intel is the clearest demonstration in this document of what the project's
discipline buys.

Intel's trailing economics are negative: its latest-year normalized operating
margin is below zero, and its projected terminal-year cash flows don't cover
its debt. Our engine returns **two labeled, honest unavailable states**:

> **DCF (Gordon):** negative equity — "projected cash flows don't cover its
> debt — enterprise value falls short of net obligations, leaving nothing for
> shareholders."
>
> **EPV:** unavailable (`epv_negative_earnings`) — "normalized operating
> margin is negative — capitalizing negative earnings is a sign error, not a
> valuation."

The comparison site prints, for the same company on the same date:

> **EPV: −$30.10 per share.  Fair Value: −$11.19 per share.**

A negative perpetuity is not a valuation; it is a sign error dressed as a
number, and this engine's methodology explicitly refuses to print one
(methodology: `epv_method`, terminal-anchor rules). One of these two tools
handled Intel correctly, and it is the difference a finance-literate reviewer
notices first.

## DCF vs. DCF

Per share; gap vs. the 2026-08-14 close. "Ours" is the Gordon leg under
auto-classified profiles and fully derived defaults — no hand edits.

| Ticker | Price | Ours (Gordon) | VI.io DCF 5y | VI.io DCF 10y | Analyst PT (n) |
|---|---|---|---|---|---|
| ADBE | 264.03 | 502 (+90%) | 324 (+23%) | 390 | 270 (40) |
| TXN  | 279.72 | 63 (−77%) | 128 (−54%) | 160 | 324 (36) |
| INTC | 102.53 | negative equity (labeled) | 11 (−90%) | 14 | 115 (48) |
| CL   | 91.92  | 91 (−1%) | 98 (+7%) | 112 | 99 (21) |
| LOW  | 218.41 | 189 (−14%) | 279 (+28%) | 304 | 261 (35) |
| CSCO | 111.68 | 42 (−63%) | 107 (−4%) | 119 | 135 (26) |
| QCOM | 165.84 | 102 (−39%) | 207 (+25%) | 274 | 193 (37) |
| UNP  | 293.76 | 167 (−43%) | 313 (+7%) | 330 | 329 (25) |
| MRK  | 135.84 | 94 (−31%) | 143 (+5%) | 180 | 137 (28) |

**The structural difference, plainly:** our growth defaults are derived from
*reported history* by documented rules; forward-estimate tools (including,
apparently, the comparator) lean on analyst projections. For CSCO, QCOM, and
UNP — names where the street prices a re-acceleration that history hasn't
printed yet — that one difference explains most of the gap. This is a design
stance (derivable, auditable, no hand-picked growth), not an accident, and it
reads conservative almost everywhere.

The exception is ADBE, where the compounder profile produces the most
aggressive number in the room: $502 against the comparator's $324 and a
40-analyst consensus of $270. The decomposition audit (2026-08-16) resolved
where the figure comes from. Under house-default conventions — 5-year
horizon, linear fade, 2.5% terminal cap — our engine values ADBE at **$315
(+19%)**, nearly identical to the comparator's 5-year model ($324, +23%):
two independent implementations agree at the same conventions. The entire
remaining gap is the compounder profile's two levers, both disclosed on the
methodology page: terminal growth at the 10Y instead of the house cap
(+$118/share alone) and the 10-year horizon (+$61 alone; $502 with both and
their interaction). The half-cosine fade shape contributes ~$1 — its
justification is kink-free structure, not magnitude. The terminal-growth
lever's impact scales with 1/(WACC − g), so it is largest exactly for
low-WACC compounders; this is the aggressive edge of a published constraint
(g ≤ 10Y, Damodaran), applied at the boundary for filers whose history
earned the classification, with the house-cap disclosure firing on every
such default. We are above the market on ADBE because the model believes
ADBE's own filed growth history and prices its durability at the published
ceiling; the market prices forward risk the filings have not printed. Both
positions are stated; neither is tuned toward the other.

Directional agreement with the comparator's primary (5y) model: same side of
the market price on ADBE, TXN, INTC, and CSCO; opposite side on CL (borderline),
LOW, QCOM, UNP, and MRK — in every disagreement, ours is the lower number.

## EPV vs. EPV

Two independent implementations of the same Greenwald-family idea: no-growth
earnings capitalized at the cost of capital.

| Ticker | Ours | VI.io EPV | Gap |
|---|---|---|---|
| CL   | 52.07 | 53.21 | within 2% |
| ADBE | 187.01 | 205.33 | within 9% |
| QCOM | 84.40 | 73.55 | ours +15% |
| CSCO | 29.51 | 39.18 | ours −25% |
| UNP  | 124.77 | 168.63 | ours −26% |
| LOW  | 134.61 | 213.26 | ours −37% |
| MRK  | 60.19 | 114.22 | ours −47% |
| TXN  | 56.52 | 21.21 | ours +167% |
| INTC | unavailable, labeled | −$30.10 | both non-positive |

Where the business is simple (CL, ADBE), the two implementations land within
single digits of each other from completely independent code — good external
evidence that the perpetuity mechanics are right.

The large divergences have identifiable candidate causes:

- **TXN (ours 2.7× theirs) — Inference:** their EPV appears to charge
  something like *actual* capex against earnings, so TXN's fab-buildout
  super-cycle (capex ≈ 3× D&A) crushes their number to $21 — pricing a
  temporary investment surge as a permanent cost. Ours deliberately sets
  maintenance capex = D&A (a documented simplification; methodology
  `epv_maintenance_capex`), yielding $57, and handles the capex surge where it
  belongs — the `reinvestment_heavy` modifier on the DCF side. We consider our
  treatment more defensible here, while noting it is the *less* conservative
  one for capex-cycle names.
- **MRK (ours −47%) — Inference:** our trailing-3y margin normalization
  absorbs acquired-IPR&D charge years that their normalization apparently
  scrubs. This is a real v1 limitation — no non-recurring-item scrubbing —
  already documented in `known-limitations.md`, and it cuts against us
  (too *low*, i.e., conservative) rather than flattering us.
- **LOW / UNP / CSCO (ours −25% to −37%) — Inference:** consistent with some
  combination of the same normalization conservatism, our marginal (not
  effective) tax rate, and bridge differences (we count only cash above an
  operating floor as excess; the standard construction adds back all cash).

Both implementations being paywalled-opaque in different directions is exactly
why our methodology page and workbook show every intermediate number.

## The refusals, and the build rate

**9 of 13 fresh tickers built.** The four refusals, honestly:

| Ticker | Refusal | Class |
|---|---|---|
| ORCL | H3 statement tie-out failed — assembled statements don't reconcile | validation refusal |
| UBER | H1 statement tie-out failed | validation refusal |
| LLY  | required item `capex` unmapped for FY2025 (tag-chain gap) | mapping gap — fixable |
| NKE  | coverage floor: 56% of assets map to named items (floor 60%) | coverage refusal |

Every popular site prints values for all four. Refusing loudly rather than
modeling numbers that don't reconcile is the project's stated trade
(non-negotiable #3), but the build rate is the honest cost of it, and it is a
different number from the hardened diagnostic universe's 23-of-27 — that
universe's mapping gaps had already been fixed. A batched chain round against
never-touched tickers is queued to measure and close what's closeable.

## Sources

- [ValueInvesting.io — ADBE intrinsic value](https://valueinvesting.io/ADBE/valuation/intrinsic-value)
  (and the same-pattern pages for TXN, INTC, CL, LOW, CSCO, QCOM, UNP, MRK),
  fetched 2026-08-16
- [stockanalysis.com](https://stockanalysis.com/stocks/adbe/) — analyst
  consensus targets per ticker, fetched 2026-08-16
- [Wall Street Prep — the EPV construction](https://www.wallstreetprep.com/knowledge/earnings-power-value-epv/)
- [Simply Wall St — ADBE](https://simplywall.st/stocks/us/software/nasdaq-adbe/adobe)
  (single figure via search snippet; site blocks automated access)
- Alpha Spread and GuruFocus: blocked (HTTP 403); no values collected
