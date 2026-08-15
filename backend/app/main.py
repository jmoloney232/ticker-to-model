"""FastAPI layer (specs/06-webapp.md) — a mechanical translation of the CLI
into HTTP. No valuation logic lives here: derive → preset → overrides →
build_model, exactly the CLI's layering, with the serializer translating
ModelResult into the JSON contract.

Status discipline (owner rule): refusals, unsupported filers, unavailable
legs, and no-solution reverse solves are 200s with machine-readable reasons.
HTTP errors are for actual failures only — unknown ticker (404), malformed
request or invalid assumption (400), upstream unreachable with nothing to
fall back to (503).
"""

from __future__ import annotations

import io
import os
from datetime import date

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from engine.assumptions import derive_assumptions
from engine.dcf import build_model
from engine.errors import InvalidAssumptionError, PresetUnavailableError
from engine.presets import (
    PRESETS_PATH,
    apply_preset,
    decode_assumption_set,
    encode_assumption_set,
    load_presets,
)
from engine.profile import parse_profile
from ingest.errors import EdgarUnavailableError, UnknownTickerError
from market.errors import MarketDataError
from market.provider import market_today

from .serialize import refusal_response, serialize_model, serialize_preset
from .state import REFUSAL_ERRORS, CompanyStore


class ModelRequest(BaseModel):
    preset: str | None = None
    overrides: dict[str, float | bool] | None = None
    profile: str | None = None               # reassignment tag; None = auto
    code: str | None = None                  # compact assumption-set code
    valuation_date: date | None = None


class CodeRequest(BaseModel):
    preset: str | None = None
    overrides: dict[str, float | bool] | None = None
    profile: str | None = None


def _prod_dependencies():
    """Server-side clients from env vars — keys never reach the browser
    (CLAUDE.md secrets rule). One shared client per upstream; the SqliteCache
    under them persists across restarts and respects EDGAR etiquette."""
    from cli import load_dotenv
    from ingest.cache import SqliteCache
    from ingest.edgar import EdgarClient
    from market.alpaca import AlpacaClient
    from market.fred import FredClient
    from market.provider import LadderedProvider

    load_dotenv()
    cache = SqliteCache(os.environ.get("CACHE_DB_PATH", "cache.sqlite"))
    edgar = EdgarClient(user_agent=os.environ.get("EDGAR_USER_AGENT", ""),
                        cache=cache)
    provider = LadderedProvider(
        AlpacaClient(os.environ.get("ALPACA_API_KEY_ID", ""),
                     os.environ.get("ALPACA_API_SECRET_KEY", "")),
        FredClient(os.environ.get("FRED_API_KEY", "")), cache=cache)
    return (lambda _t: edgar), (lambda _t: provider)


def create_app(edgar_for=None, provider_for=None,
               store_ttl: float = 3600.0) -> FastAPI:
    if edgar_for is None or provider_for is None:
        edgar_for, provider_for = _prod_dependencies()
    app = FastAPI(title="Ticker to Model API", docs_url="/api/docs",
                  openapi_url="/api/openapi.json")
    app.state.store = CompanyStore(edgar_for, provider_for, ttl=store_ttl)
    app.state.presets = load_presets()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.environ.get("FRONTEND_ORIGIN",
                                      "http://localhost:5173")],
        allow_methods=["*"], allow_headers=["*"])

    # ── shared assembly ─────────────────────────────────────────────────────

    def _resolve_inputs(body: ModelRequest
                        ) -> tuple[str | None, dict, str | None, date]:
        """code + explicit fields → (preset_name, overrides, profile, vd);
        explicit wins over the code on conflict (same rule as the CLI)."""
        code_preset, code_overrides, code_profile = (None, {}, None)
        if body.code:
            try:
                code_preset, code_overrides, code_profile = \
                    decode_assumption_set(body.code)
            except ValueError as exc:
                raise HTTPException(400, detail=str(exc)) from exc
        preset_name = body.preset or code_preset
        profile = body.profile or code_profile
        if profile is not None:
            try:
                parse_profile(profile)
            except ValueError as exc:
                raise HTTPException(400, detail=str(exc)) from exc
        overrides = {**code_overrides, **(body.overrides or {})}
        return (preset_name, overrides, profile,
                body.valuation_date or market_today())

    def _build(ticker: str, body: ModelRequest):
        """Returns (payload dict) — refusals included — or raises HTTP errors
        for actual failures."""
        preset_name, overrides, profile, vd = _resolve_inputs(body)
        preset = None
        if preset_name:
            if preset_name not in app.state.presets:
                raise HTTPException(
                    400, detail=f"unknown preset {preset_name!r} "
                                f"(available: {', '.join(app.state.presets)})")
            preset = app.state.presets[preset_name]
        try:
            entry = app.state.store.get(ticker, vd)
        except UnknownTickerError as exc:
            raise HTTPException(404, detail=exc.user_message) from exc
        except EdgarUnavailableError as exc:
            raise HTTPException(503, detail=exc.user_message) from exc
        except MarketDataError as exc:
            raise HTTPException(503, detail=exc.user_message) from exc
        if entry.refusal is not None:
            return refusal_response(entry.refusal), None
        history, market = entry.history, entry.market
        try:
            assumptions = None
            if preset is not None:
                assumptions = apply_preset(
                    derive_assumptions(history, market,
                                       profile=profile or "auto"),
                    preset, history, market, vd)
            model = build_model(history, market, valuation_date=vd,
                                overrides=overrides or None,
                                assumptions=assumptions,
                                profile=profile or "auto")
        except PresetUnavailableError as exc:
            return {"status": "preset_unavailable",
                    "reason": {"code": "preset_unavailable",
                               "message": exc.user_message,
                               "detail": dict(exc.detail)}}, None
        except InvalidAssumptionError as exc:
            raise HTTPException(400, detail=exc.user_message) from exc
        reverse = app.state.store.reverse(ticker, vd)
        return serialize_model(model, preset, overrides, reverse,
                               profile=profile), model

    # ── endpoints ───────────────────────────────────────────────────────────

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/presets")
    def presets():
        return {"presets": [serialize_preset(p)
                            for p in app.state.presets.values()]}

    @app.get("/api/audit-guide")
    def audit_guide():
        """The committed audit guide (docs/financial-assumptions.md),
        rendered on the Audit tab. Markdown as data — the frontend renders
        a safe subset, no HTML passes through."""
        from pathlib import Path
        path = (Path(__file__).resolve().parents[2] / "docs"
                / "financial-assumptions.md")
        if not path.exists():
            raise HTTPException(
                404, detail="The audit guide isn't present in this "
                            "deployment.")
        return {"markdown": path.read_text()}

    @app.get("/api/methodology")
    def methodology():
        doc = yaml.safe_load(
            (PRESETS_PATH.parent / "methodology.yaml").read_text())
        return {"meta": doc.get("meta", {}),
                "conventions": doc.get("conventions", []),
                "presets": [serialize_preset(p)
                            for p in app.state.presets.values()]}

    @app.post("/api/model/{ticker}")
    def model_post(ticker: str, body: ModelRequest):
        payload, _ = _build(ticker, body)
        return payload

    @app.get("/api/model/{ticker}")
    def model_get(ticker: str, code: str | None = None,
                  preset: str | None = None, profile: str | None = None,
                  valuation_date: date | None = None):
        payload, _ = _build(ticker, ModelRequest(
            preset=preset, code=code, profile=profile,
            valuation_date=valuation_date))
        return payload

    @app.get("/api/reverse/{ticker}")
    def reverse(ticker: str, valuation_date: date | None = None):
        vd = valuation_date or market_today()
        try:
            entry = app.state.store.get(ticker, vd)
        except UnknownTickerError as exc:
            raise HTTPException(404, detail=exc.user_message) from exc
        except (EdgarUnavailableError, MarketDataError) as exc:
            raise HTTPException(503, detail=exc.user_message) from exc
        if entry.refusal is not None:
            return refusal_response(entry.refusal)
        solves = app.state.store.reverse(ticker, vd)
        return {"status": "ok", "ticker": ticker.upper(),
                "valuation_date": vd.isoformat(),
                "solves": {f: {"derived": r.derived, "implied": r.implied,
                               "status": r.status,
                               "target_price": r.target_price}
                           for f, r in solves.items()}}

    @app.get("/api/workbook/{ticker}.xlsx")
    def workbook(ticker: str, code: str | None = None,
                 preset: str | None = None, profile: str | None = None,
                 valuation_date: date | None = None):
        """Same layering as /api/model — what you download equals what you
        see, because both come from the same ModelResult."""
        from excel import write_workbook
        payload, model = _build(ticker, ModelRequest(
            preset=preset, code=code, profile=profile,
            valuation_date=valuation_date))
        if model is None:                      # refusal → no workbook to give
            raise HTTPException(
                409, detail=payload["reason"]["message"])
        active = payload.get("preset")
        preset_obj = (app.state.presets.get(active["name"])
                      if active else None)
        buf = io.BytesIO()
        write_workbook(model, buf, preset=preset_obj)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type=("application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet"),
            headers={"Content-Disposition":
                     f'attachment; filename="{ticker.upper()}.xlsx"'})

    @app.post("/api/code")
    def code_encode(body: CodeRequest):
        return {"code": encode_assumption_set(body.preset,
                                              body.overrides or None,
                                              body.profile)}

    @app.get("/api/code/{code}")
    def code_decode(code: str):
        try:
            preset, overrides, profile = decode_assumption_set(code)
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        return {"preset": preset, "overrides": overrides, "profile": profile}

    async def refusal_handler(_request: Request, exc):
        # belt-and-braces: a refusal raised outside the store still returns
        # the structured 200, never a 500
        from fastapi.responses import JSONResponse
        return JSONResponse(refusal_response(exc))

    for cls in REFUSAL_ERRORS:
        app.add_exception_handler(cls, refusal_handler)

    return app
