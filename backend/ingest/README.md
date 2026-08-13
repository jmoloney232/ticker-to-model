# ingest — EDGAR fetch, tag mapping, period selection, validation

Turns a ticker into a clean, validated `FinancialHistory`: EDGAR `companyfacts` fetch,
canonical tag mapping via `schema.yaml` (the machine-readable source of truth for line
items and fallback chains), restatement resolution, fiscal-calendar handling, and
financial-company rejection. Spec: [`specs/01-ingest.md`](../../specs/01-ingest.md) and
[`specs/02-schema.md`](../../specs/02-schema.md). Built in phase 1 (first).
