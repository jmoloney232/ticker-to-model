# excel — formula-driven workbook writer

Turns engine output into an .xlsx where every output cell is a live formula (the
project's #1 non-negotiable). Named ranges for assumptions, no circular references,
recalc-on-open, and a CI parity test that recalculates the workbook and diffs it against
engine values. Spec: [`specs/05-excel.md`](../../specs/05-excel.md). Built in phase 3.
