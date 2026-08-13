# app — FastAPI layer

HTTP routes, request/response serialization, dependency wiring. No business logic lives
here: routes call `ingest`, `market`, `engine`, and `excel` and translate their results.
Spec: [`specs/06-webapp.md`](../../specs/06-webapp.md). Built in phase 4 (last).
