# Spec 05 — Excel writer (as built, phase 3; owner layout approved 2026-08-14)

Renders a `ModelResult` as a formula-driven workbook — **the project's #1
non-negotiable**: clicking FY5 EBITDA must show a formula referencing other cells,
never a pasted number. openpyxl; engine remains the source of truth via the
round-trip gate below.

**The governing rule (owner):** every calculated cell contains a live Excel formula.
The only hardcoded values are input assumptions, market data, and reported
historical actuals. If a value can be computed from other cells, it is computed in
Excel, not written in by Python.

## Inputs

- `ModelResult` (+ the active `Preset`, for its name and rationale)
- `methodology.yaml` and `presets.yaml` (rendered as the Methodology sheet)

## Outputs

- `.xlsx` file + a **cell map** (`{logical key: (sheet, coordinate)}`) returned by
  `write_workbook()` — consumed by the round-trip test; not stored in the workbook.

## Workbook structure (tab order)

| Sheet | Contents | Cell nature |
|---|---|---|
| Cover | Company/ticker/meta, valuation date, price, headline per-share values with vs-price deltas, WACC, implied cross-checks, **active preset + rationale**, **live checks summary**, **full warnings block** (inherited ingest + market + engine, unmapped items grouped), staleness labels, color-convention legend | Formulas referencing Valuation + text |
| Assumptions | One row per assumption: label, **value (named range, blue on yellow)**, unit, **provenance (derived / preset:<name> / user)**, derived default (when it differs), derivation/rule text. Grouped: market data, growth, cost structure, capital intensity, working capital, taxes, payout, interest, cost of capital, terminal, exit & bridge, toggles. This sheet is the control panel — changing any cell moves every downstream number | **Inputs (blue)** |
| Historical | As-reported IS/BS/CF, fiscal-year columns, FYE date row; a computed gross-debt row (formula). Unmapped (zero_logged) items: gray-italic 0 **plus the warning text in the notes column** — never a bare zero indistinguishable from a real one; ingest-derived values italic with a note; absent items blank | Actuals (blue literals) |
| Model | FY0 **anchor column of live `=N(Historical!…)` links (green)** + FY1–FY5 projected IS / BS / CF, all driven by named ranges: growth & tax fade rows, D&A-inclusive cost lines incl. the unclassified-costs closure, beginning-balance interest, held-flat lines as `=$B$row`, the unattributed-carryforward line computed from anchors, **cash as the plug**, indirect CF. Ends with the **LIVE CHECKS block**: per-year BS tie and CF tie rows plus OK/FAIL summary formulas (tolerance $1) | **Formulas only** |
| Valuation | WACC build-up (nested-IF synthetic-spread lookup generated from the engine's `SPREAD_TABLE` — parity by construction; live `kd_synthetic` toggle), stub/exponent cells from `valuation_date` − `fy0_end`, UFCF schedule referencing Model, both TVs **with semantic guards in the formulas**, implied cross-checks, EV→equity bridge ×2 legs, per-share | **Formulas only** |
| Sensitivity | Two live grids (WACC × g 5×9 at 25 bp g-steps, WACC × multiple 5×5) re-centering on current assumptions. **WACC × g re-projects per g column** (growth path fades INTO g — engine semantics) via visible, labeled helper blocks (growth/revenue/EBIT/capex/D&A/PP&E/NWC/ΔNWC/UFCF per g, + NOPAT₆ row); each grid cell is a self-contained `SUMPRODUCT(ufcf, POWER(1+w,−t)) + TV + bridge_adj)/shares` formula referencing its row/column headers. WACC × multiple re-prices the base projection. Unavailable legs → explanatory text, no grid | **Formulas only** |
| Methodology | Every convention (default/derivation/tradeoff) from methodology.yaml + every preset (title/rationale/field rules) from presets.yaml | Text |

## Named-range scheme (owner-approved)

**The named range for each assumption is the engine field name verbatim** —
`terminal_growth`, `capex_pct`, `dso`, `beta`, `midyear`, `sbc_addback`, … — one
identifier across engine, CLI, provenance, methodology, and workbook. Extras:
`market_price`, `valuation_date` (Assumptions) and `fy0_end` (Historical). All
workbook-scoped, absolute. Booleans are TRUE/FALSE cells consumed via `IF(...)`
(`midyear`, `sbc_addback`, `kd_synthetic` are live toggles; `beta_adjusted` is
documentation-only — its state is baked into the effective `beta` at generation,
stated in its derivation column, because re-deriving Blume in-sheet would
double-apply preset/override beta logic).

## Semantic guards live in the formulas

The engine's unavailable states are not special-cased at write time — the same
conditions are encoded in the formulas, so user edits reproduce engine behavior:

- Gordon TV: `IF(NOPAT₆≤0, "unavailable — negative terminal NOPAT anchor", IF(g≥WACC,
  "blocked — terminal g must be below WACC", …))`; exit TV guards `exit_multiple`
  blank and `EBITDA₅≤0`. Text propagates via `ISNUMBER` wrappers through EV → equity
  → per-share — **never an Excel error value**, whether at generation or after a
  breaking user edit. Cover carries live `g<WACC` and `RR<1` checks.
- ROIC fallback: `IF(OR(terminal_roic="", terminal_roic≤g), WACC, terminal_roic)` —
  the sheet reproduces the engine's value-neutral fallback (and the same logic per
  sensitivity cell at that cell's WACC).

## Technical constraints (owner)

No macros, no external links, no volatile functions (no OFFSET/INDIRECT/TODAY —
`valuation_date` is a stamped input), no circular references, no iterative
calculation, no dynamic-array-only functions. Function set actually used: arithmetic,
`SUM`, `SUMPRODUCT`, `POWER`, `IF`, `AND`, `OR`, `MIN`, `MAX`, `ABS`, `N`,
`ISNUMBER`, `TEXT` — recalculates in Excel, Google Sheets, and LibreOffice.
`fullCalcOnLoad` set (openpyxl stores no cached values). ~40KB typical, no images.

## Styling

Blue = hardcoded input/actual (assumption cells additionally light-yellow fill);
black = formula; green = pure cross-sheet link; gray-italic = unmapped-zero;
section headers bold on gray. USD millions via comma-scaled formats, negatives in
parentheses, percent/multiple/days/per-share formats per unit, frozen panes on
every data sheet, gridlines off, FY columns labeled `FY2025` / `FY2026E`.

## Error cases

Generation is deterministic from `ModelResult`; failures are Python exceptions at
write time (missing history rows resolve to honest `=0` anchors via `N()`;
None-valued assumptions render as blank inputs consumed by the `=""` guards).

## How tested (the owner's phase gate — unit tests are NOT sufficient)

Both gates run against **LibreOffice headless** (`soffice --headless --convert-to
xlsx`), a real spreadsheet engine, verified to honor `fullCalcOnLoad` by probe.
If LibreOffice is absent the tests **skip loudly stating the gate did not run** —
never silently substituted with a weaker check.

1. **Round-trip parity:** for MSFT (clean, by_function), MCD (by_nature +
   unclassified-costs + lease warnings), GOOGL (share_count_derived): write →
   recalculate → read back → diff every mapped cell against the engine at rel 1e-6
   (abs floor 1e-4): all Model statement rows × FY1–5, the full UFCF schedule, WACC
   build, both TVs and bridges, both grids cell-by-cell (engine `None` ↔ `"—"`),
   and the live checks reading OK. Plus KHC: unavailable exit leg renders as
   explanatory text, Gordon's negative-equity value matches the engine, zero Excel
   error values anywhere.
2. **Liveness:** edit `terminal_growth` in the saved file → recalc → per-share
   equals the engine under the same override (exact expected amount, not just
   direction); same for a cost-ratio edit, the SBC toggle, and the **mid-year
   toggle** (Gordon exponent moves, exit exponent doesn't — the deliberate
   asymmetry held live). A breaking edit (g=20%) flips the sheet to "blocked" text
   and the Cover check to FAIL, with no error values.

The round-trip subsumes the old write-time graph check: LibreOffice recalculation
with matching values is not possible with circular references or unsupported
functions.

## Interface

`python -m cli TICKER --excel PATH [--preset NAME] [--set k=v ...]` — same
derive → preset → override layering and provenance as every other surface.
`write_workbook(model, path, preset=None)` from `backend/excel/`.
