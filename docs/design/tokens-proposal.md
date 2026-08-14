# Token extraction — proposal (phase 4, part 2 preamble; awaiting owner review)

Source of truth: `docs/design/valuation-tool-directions.dc.html` (the mockup) over
the Industry design system (`styles.css`). Every value below is lifted from the
mockup's inline styles — nothing invented, nothing substituted. Implementation
shape: one `frontend/src/tokens.css` defining these as CSS custom properties
layered over the Industry stylesheet; components use `var(--…)` only (the
adherence lint forbids raw hex/px).

## Color

| Token | Value | Industry mapping | Used for (mockup evidence) |
|---|---|---|---|
| `--ink` | `#1d1f20` | `--color-text` | text, filled axis marker, base-cell ring |
| `--paper` | `#f2f2f3` | `--color-bg` | board ground, inputs, reversed-field type |
| `--panel` | `#e9e9ea` | `--color-surface` | header bands, rails, table heads |
| `--steel` | `#5980a6` | `--color-accent` | primary button, heat fill, edited marks, hero bar |
| `--steel-deep` | `#416180` | `--color-accent-700` | accent-colored text on light: links, market figures, +deltas, edited values |
| `--steel-sky` | `#94bce3` | `--color-accent-400` | accent on the dark field (market line, implied figure) |
| `--steel-night` | `#1d2d3d` | `--color-accent-900` | reversed steel field (1b header, 1c hero band) |
| `--warn` | `#a8752a` | **new** | caveat rows (△) — "color as data only" |
| `--down` | `#9e4b3c` | **new** | a value below market |
| `--down-on-dark` | `#e0a58a` | **new** | below-market delta on the steel-night field |

Hairlines and muted text are ink (or paper, on dark) at fixed alphas — the mockup
uses a tight, repeated set worth naming rather than scattering `rgba()`:

- Lines: `--line-faint` .07 (row rules) · `--line-soft` .10–.12 (cell dividers) ·
  `--line-mid` .16–.20 (pane borders) · `--line-strong` .35 (axis/table heads) ·
  `--line-frame` .45 (ticker input border).
- Text tiers: `--text-2` .72 (warning body) · `--text-3` .55–.60 (labels, sources)
  · `--text-4` .45–.50 (axis ticks, hints). Dark-field equivalents use paper at
  .75/.60/.50.

## Type

Three families — two from Industry, one **new**:

- `--font-heading` Barlow Condensed 600 (labels/kickers), `--font-body` Barlow —
  unchanged from the system.
- `--font-mono` **IBM Plex Mono 400/500/600** — every figure in the mockup;
  the system has no tabular face. Google Fonts, self-explanatory addition the
  mockup's brief already argues for. Needs: font import + adherence config
  `fontFamilies` + token registration. **This is a DS extension — flagging for
  sign-off, per the no-new-dependencies rule.**

Scale (all from mockup shorthands; `tabular-nums` on every mono use):

| Token | Spec | Where |
|---|---|---|
| `--type-label` | cond 600 9.5–10px, tracking .10–.12em, uppercase | section/column headers, kickers |
| `--type-body-xs` | Barlow 400 9.5–10px | inline rules, hints, axis notes |
| `--type-body-sm` | Barlow 400 10.5–11px | rationales, warning text |
| `--type-body` | Barlow 400 11.5–12.5px | row labels, prose |
| `--type-cell` | mono 500 12.5–13px | assumption values |
| `--type-grid` | mono 500 10–10.5px | sensitivity cells, axis figures |
| `--type-fig` | mono 600 14–15px | price, ticker box |
| `--type-kpi-sm / -md / -lg` | mono 600 20 / 26 / 30px, −.02em | bar labels, result plates |
| `--type-kpi-xl` | mono 600 34px, −.02em | 1a flanking figures |
| `--type-hero` | mono 600 52px, −.03em | 1c reversed-field figures |
| `--type-glyph` | mono 500 9–11px | ■ □ ● ƒ △ ○ marks |

## Density

Row pad-y 3–3.5px, cell pad-x 12–16px, pane pads 10–18px — these sit on the
Industry `--space` scale closely enough to reuse it (`--space-1` 3.4 ≈ row-y,
`--space-3` 10.2 / `--space-4` 13.6 ≈ pane pads, `--space-6` 20.4 ≈ hero pads).
No new spacing tokens proposed; the two exact grid column specs (52px WACC
label column, `repeat(10,1fr)` cells) stay as component-level constants.

## Treatments (component-level, tokenized where colored)

- **Hatch** (projected/hypothetical areas — the 1a gap band, 1c method bars):
  `repeating-linear-gradient(135deg, <tone> 0 2px, transparent 2px 6–7px)`.
- **Heat** (sensitivity): steel at alpha `.04 + t × .30`, t = min-max normalized
  cell value; text flips to `--steel-night` above t ≈ .78; base case ringed
  `1.5px solid var(--ink)`, offset −1. Modes per the mockup props:
  heat / threshold (≥ market price) / none.
- **Glyphs**: ■ derived · □ preset · ● edited (steel) · ƒ computed/locked ·
  △ caveat (`--warn`) · ○ inactive preset — one constants module, mono face.
- **Ticker input**: mono 600 15px on paper, `--line-frame` border, steel caret.
- **Registration marks** on boards and the primary button — the Industry
  `.blueprint` grammar, kept.
- **Focus/hover/selection**: inherited from the Industry stylesheet unchanged.

## What I am NOT extracting (design fiction → real contract)

The mockup's field list, preset names, and several finance labels are placeholder
fiction; the UI binds to the API contract instead: real presets from
`/api/presets` (derived, market_implied, street_convention, downside — titles and
rationales from presets.yaml), real assumption rows with provenance from
`/api/model`, real warnings. Items in the mockup with no engine counterpart
(Capitalize R&D, FY2–3/FY4–5 growth splits, leases-in-debt toggle, 20% cap, 5y
beta) render as their real equivalents, not as new features.

## Open decisions for the owner

1. **Direction: 1a, 1b, 1c, or a named hybrid** (the mockup itself suggests
   "1a's axis band on 1c's reversed field").
2. **Sensitivity grid shape**: mockup is 5 WACC × 10 g at 25bp g-steps; the
   engine emits 5 × 5 at 50bp. Widening is a one-line engine-constant change
   (`G_STEP`/offsets) that recomputes honestly per column — but it's a stated
   convention, so it's yours to call. UI renders whatever the API sends.
3. **IBM Plex Mono + the three semantic colors** as DS extensions (above).
