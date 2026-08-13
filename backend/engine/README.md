# engine — projections, FCF, WACC, DCF, sensitivities

Pure functions only: history + assumptions in, model out. No I/O, no web framework, no
imports from `app`/`ingest`/`market` beyond typed dataclasses. `methodology.yaml` here is
the single source of truth for every valuation convention — rendered on the website's
Methodology page, in the workbook's Methodology sheet, and referenced throughout
[`specs/04-engine.md`](../../specs/04-engine.md). Built in phase 2.
