"""ModelResult → the JSON contract (specs/06-webapp.md).

The contract rules (owner, phase 4):
- Layering and provenance survive intact: every assumption carries value,
  unit, provenance, the derived default, and the rule text.
- Every inherited warning passes through STRUCTURED (origin/code/message/
  detail), never concatenated into a string.
- Unavailable states are data with a machine-readable reason, not errors.
- No valuation math is left to the browser: vs-price deltas, TV share of EV,
  and provenance counts are computed here, server-side, from engine outputs.
"""

from __future__ import annotations

import dataclasses

from engine.models import Bridge, ModelResult, TerminalLeg
from engine.presets import Preset, encode_assumption_set
from engine.reverse import ImpliedResult


def _label(name: str) -> str:
    return name.replace("_", " ").capitalize()


def assumption_rows(m: ModelResult) -> list[dict]:
    rows = []
    for f in m.assumptions.fields.values():
        prov = f.provenance
        if prov == "user":
            rule = f.derivation
        elif prov.startswith("preset"):
            rule = f"{f.preset_note} — {f.derivation}"
        else:
            rule = f.derivation
        rows.append({
            "name": f.name,
            "label": _label(f.name),
            "value": f.effective,
            "unit": f.unit,
            "provenance": prov,
            "derived_default": f.value,
            "rule": rule,
            "editable": f.name != "revenue_cagr_uncapped",   # display-only field
        })
    return rows


def provenance_counts(m: ModelResult) -> dict[str, int]:
    counts = {"derived": 0, "preset": 0, "user": 0}
    for f in m.assumptions.fields.values():
        p = f.provenance
        counts["preset" if p.startswith("preset") else p] += 1
    return counts


def warnings_out(m: ModelResult) -> list[dict]:
    out = []
    for w in m.history.warnings:
        out.append({"origin": "ingest", "code": w.code, "message": w.message,
                    "fiscal_year": w.fiscal_year, "item": w.item,
                    "detail": dict(w.detail)})
    for w in m.market.warnings:
        out.append({"origin": "market", "code": w.code, "message": w.message,
                    "fiscal_year": None, "item": None, "detail": dict(w.detail)})
    for w in m.warnings:
        out.append({"origin": "engine", "code": w.code, "message": w.message,
                    "fiscal_year": None, "item": None, "detail": dict(w.detail)})
    return out


def _leg(m: ModelResult, method: str, price: float) -> dict:
    if method in m.terminal:
        leg: TerminalLeg = m.terminal[method]
        bridge: Bridge = m.bridges[method]
        ev = bridge.enterprise_value
        return {
            "available": True,
            "value_per_share": bridge.value_per_share,
            "vs_price": bridge.value_per_share / price - 1,
            "enterprise_value": ev,
            "equity_value": bridge.equity_value,
            "tv_at_fyeN": leg.value_at_fyeN,
            "tv_pv": leg.pv,
            "tv_exponent": leg.exponent,
            "tv_share_of_ev": leg.pv / ev if ev > 0 else None,
            "tv_detail": dict(leg.detail),
            "bridge": [{"name": i.name, "value": i.value, "source": i.source,
                        "note": i.note} for i in bridge.items],
        }
    # honest unavailable state, reason attached (owner rule: data, not error)
    for w in m.warnings:
        if w.code == "terminal_anchor_negative" and w.detail.get("leg") == method:
            return {"available": False,
                    "reason": {"code": "terminal_anchor_negative",
                               "message": w.message, "detail": dict(w.detail)}}
    return {"available": False,
            "reason": {"code": "exit_multiple_unavailable",
                       "message": "No exit multiple — FY0 EBITDA ≤ 0, so a "
                                  "current EV/EBITDA cannot be derived.",
                       "detail": {}}}


def _reverse_out(reverse: dict[str, ImpliedResult] | None) -> dict | None:
    if reverse is None:
        return None
    return {field: {"derived": r.derived, "implied": r.implied,
                    "status": r.status, "target_price": r.target_price}
            for field, r in reverse.items()}


def _filing_basis(m: ModelResult) -> dict | None:
    fy0 = m.history.periods[-1]
    fact = fy0.income.get("revenue")
    if fact is None:
        return None
    return {"fiscal_year": fy0.fiscal_year,
            "accession": fact.accession,
            "filed": fact.filed.isoformat() if fact.filed else None}


def _history_out(m: ModelResult) -> list[dict]:
    out = []
    for p in m.history.periods:
        period = {"fiscal_year": p.fiscal_year, "end": p.end.isoformat(),
                  "is_53_week": p.is_53_week}
        for stmt in ("income", "balance", "cashflow"):
            period[stmt] = {k: {"value": f.value, "source": f.source,
                                "restated": f.was_restated}
                            for k, f in getattr(p, stmt).items()}
        out.append(period)
    return out


def serialize_model(m: ModelResult, preset: Preset | None,
                    overrides: dict | None,
                    reverse: dict[str, ImpliedResult] | None) -> dict:
    price = m.market.price.value
    h = m.history
    cov = h.coverage
    return {
        "status": "ok",
        "ticker": m.ticker,
        "valuation_date": m.valuation_date.isoformat(),
        "code": encode_assumption_set(
            preset.name if preset else None, overrides or None),
        "company": {
            "name": h.company.name, "cik": h.company.cik,
            "sic": h.company.sic, "sic_description": h.company.sic_description,
            "fye_anchor": h.company.fye_anchor,
            "cost_structure": h.cost_structure,
            "filing_basis": _filing_basis(m),
        },
        "market": {
            "price": {"value": price, "as_of": str(m.market.price.as_of),
                      "staleness": m.market.price.staleness},
            "risk_free": {"value": m.market.risk_free.value,
                          "as_of": str(m.market.risk_free.as_of),
                          "staleness": m.market.risk_free.staleness},
            "beta": (dataclasses.asdict(m.market.beta)
                     if m.market.beta is not None else None),
        },
        "preset": ({"name": preset.name, "title": preset.title,
                    "rationale": preset.rationale} if preset else None),
        "assumptions": assumption_rows(m),
        "provenance_counts": provenance_counts(m),
        "valuation": {
            "gordon": _leg(m, "gordon", price),
            "exit_multiple": _leg(m, "exit_multiple", price),
        },
        "wacc": dataclasses.asdict(m.wacc),
        "ufcf": [dataclasses.asdict(y) for y in m.ufcf],
        "projections": [{"fiscal_year": p.fiscal_year, "fye": p.fye.isoformat(),
                         "income": p.income, "balance": p.balance,
                         "cashflow": p.cashflow} for p in m.projections],
        "crosschecks": dict(m.crosschecks),
        "sensitivity": {
            name: {"row_label": g.row_label, "col_label": g.col_label,
                   "rows": g.rows, "cols": g.cols, "cells": g.cells}
            for name, g in m.sensitivity.items()
        },
        "checks": [{"id": r.check_id, "severity": r.severity,
                    "status": r.status, "magnitude": r.magnitude,
                    "detail": r.detail} for r in m.checks.results],
        "warnings": warnings_out(m),
        "coverage": ({"assets_named_share": cov.assets_named_share,
                      "liabilities_named_share": cov.liabilities_named_share,
                      "expenses_named_share": cov.expenses_named_share}
                     if cov else None),
        "history": _history_out(m),
        "reverse": _reverse_out(reverse),
    }


def serialize_preset(p: Preset) -> dict:
    return {
        "name": p.name, "title": p.title, "rationale": p.rationale,
        "builtin": p.builtin, "applicability": dict(p.applicability),
        "fields": [{"field": f.name, "form": f.form, "rule": f.rule,
                    "value": f.value, "solver": f.solver, "target": f.target,
                    "optional": f.optional} for f in p.fields.values()],
    }


def refusal_response(exc) -> dict:
    """Refused / unsupported filers are 200s with a structured reason —
    a considered feature, not an error page (owner requirement)."""
    from ingest.errors import FinancialCompanyError, KnownUnsupportedError
    status = ("unsupported"
              if isinstance(exc, (FinancialCompanyError, KnownUnsupportedError))
              else "refused")
    code = {
        "FinancialCompanyError": "financial_company",
        "KnownUnsupportedError": "known_unsupported",
        "UnsupportedCurrencyError": "unsupported_currency",
        "InsufficientHistoryError": "insufficient_history",
        "MissingRequiredItemError": "missing_required_item",
        "ValidationFailedError": "validation_failed",
        "InsufficientCoverageError": "insufficient_coverage",
    }.get(type(exc).__name__, "refused")
    return {"status": status,
            "reason": {"code": code, "message": exc.user_message,
                       "detail": dict(exc.detail)}}
