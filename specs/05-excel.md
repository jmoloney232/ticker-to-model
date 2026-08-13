# Spec 05 — Excel writer

Renders a `ModelResult` as a formula-driven workbook — **the project's #1
non-negotiable**: clicking FY29 EBITDA must show a formula referencing other cells,
never a pasted number. openpyxl; engine remains the source of truth via a parity test.

## Inputs

- `ModelResult` + effective `Assumptions` (spec 04)
- Company metadata, validation report, warnings, staleness labels (for the Cover sheet)
- `methodology.yaml` (rendered as the Methodology sheet)

## Outputs

- `.xlsx` bytes + a **cell map manifest** (JSON: engine output field → sheet!cell) used
  by the parity test and kept out of the workbook itself.

## Workbook structure

| Sheet | Contents | Cell nature |
|---|---|---|
| Cover | Company, ticker, valuation date, price + value summary, validation tie-out table, warnings, data staleness labels | Labels + literals |
| Methodology | Every convention: default, derivation, tradeoff (from methodology.yaml) | Text |
| Assumptions | Every assumption grouped (growth, margins, working capital, capital intensity, taxes, WACC, terminal value, toggles); each row: label, **input cell (named range)**, derivation text, default value | **Inputs (blue)** |
| Hist IS / Hist BS / Hist CF | As-ingested history with fiscal-year columns; provenance notes (tag, restated flag) as cell comments | Data literals |
| Projections | Full three-statement model FY1–FY5 | **Formulas only** |
| DCF | WACC build (coverage → spread → Kd; Ke; weights), UFCF schedule, discount factors, both TVs, implied cross-checks, EV→equity bridge, value per share | **Formulas only** |
| Sensitivity | Two 5×5 grids (WACC × g, WACC × multiple) | **Formulas only** |

Structural separation (inputs / calculations / outputs) is by sheet, reinforced by
styling within sheets.

## Formula rules

- Every cell on Projections, DCF, and Sensitivity is a formula referencing named ranges
  and other cells. The **only literals** in the workbook are: assumption inputs,
  historical facts, market inputs, and labels.
- **Named ranges** (workbook-scoped) for every assumption: `RevGrowth_Y1`…`RevGrowth_Y5`,
  `GrossMargin`, `RnD_Pct`, `SGA_Pct`, `DA_PctPPE`, `Capex_Pct`, `DSO`, `DIO`, `DPO`,
  `TaxEffective`, `TaxMarginal`, `PayoutRatio`, `CashYield`, `Beta`, `ERP`, `RiskFree`,
  `TerminalG`, `ROIC_Terminal`, `ExitMultiple`, `CashFloorPct`, `ValuationDate`,
  `MidYear` (0/1), `SBCAddback` (0/1), plus `WACC` on the DCF sheet.
- **Function whitelist:** arithmetic, `^`, `SUM`, `SUMPRODUCT`, `IF`, `MIN`, `MAX`,
  `ABS`. No volatile functions — **`TODAY()` is banned**; `ValuationDate` is a stamped
  literal so the workbook reproduces the app's numbers forever. No `NPV` (it can't
  express stub/mid-year exponents). The writer hard-fails on a non-whitelisted function
  (`UnsupportedFunctionError`) — this also guarantees the parity engine can evaluate
  everything.
- **No circular references, ever.** Guaranteed upstream by beginning-of-period interest
  (spec 04); asserted here by a graph check at write time. The workbook must never
  prompt for iterative calculation.
- Toggles enter formulas arithmetically, e.g. discount exponent
  `=(t_N) - 0.5*MidYear` and SBC line `... + SBCAddback * SBC_t` — so flipping a named
  cell between 0/1 live-updates the whole model in Excel.
- Sensitivity cells are **self-contained formulas** (openpyxl cannot create Excel
  what-if data tables): each cell rebuilds PV as
  `SUMPRODUCT(UFCF_row, (1+wacc_cell)^(-exponent_row)) + TV(wacc_cell, g_or_mult)
  * (1+wacc_cell)^(-tv_exponent)` referencing its row/column headers, so the grid stays
  live when a user edits any assumption.
- `wb.calculation.fullCalcOnLoad = True` — openpyxl stores formulas without cached
  values; this forces Excel to compute on open (cells would otherwise show 0/blank).

## Styling (standard modeling conventions — the finance-literate reviewer will check)

- **Blue font on light-yellow fill = input** (assumptions and any overridable cell).
- **Black = formula** within the sheet; **green = reference to another sheet**.
- Historical data styled as data (black, no fill) with provenance comments.
- Money in $ millions, 1 decimal; percentages 1 decimal; shares in millions; negative
  numbers in parentheses; year columns labeled FY2024A / FY2025E etc. (A = actual,
  E = estimate).
- Frozen panes: header row + label column on every statement sheet.

## Invariants

- Zero literals on calculation sheets (scripted check over the manifest).
- Every named range defined, workbook-scoped, and referenced at least once.
- No external links, no VBA, no volatile functions.
- Cell-graph is acyclic (no iterative calc).
- Deterministic output: identical inputs → byte-identical workbook (zip timestamps
  normalized) — makes golden-file testing possible.

## Error cases

| Error | Trigger |
|---|---|
| `UnsupportedFunctionError` | A formula uses a non-whitelisted function (drift guard) |
| `CircularReferenceError` | Cycle detected in the write-time graph check |
| `ManifestMismatchError` | An engine output field has no mapped cell or vice versa |

## How tested

- **Parity test (the big one):** for every fixture ticker × toggle combination
  {mid-year on/off} × {SBC on/off} × {Kd synthetic/embedded}: write the workbook, load
  it with the `formulas` Python library, compute all cells, and diff every manifest cell
  against `ModelResult` at relative tolerance 1e-6. Fallback recalc engine (documented,
  used only if `formulas` lacks a needed function — which the whitelist prevents):
  LibreOffice headless re-save with forced recalculation.
- **Mid-year TV discount test:** workbook PV(TV_gordon) shifts by exactly
  (1+WACC)^0.5 when `MidYear` flips; PV(TV_exit) does not move. (Guards the deliberate
  asymmetry — worth 2–4% of value.)
- **Live-edit test:** programmatically change `RevGrowth_Y1` in the saved workbook,
  recalc, and assert FY1 revenue, EBITDA, and value per share all move — proves the
  model is genuinely live, not decorative formulas over pasted values.
- **Structure tests:** whitelist scan, acyclicity, named-range completeness,
  zero-literals-on-calc-sheets, `fullCalcOnLoad` set.
- **Manual QA checklist** (once per release): open in real Excel — no repair dialog, no
  iterative-calc prompt, values match dashboard, click-through of five random formula
  cells reads sensibly.
