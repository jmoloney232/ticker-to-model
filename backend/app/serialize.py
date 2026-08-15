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

from engine.assumptions import DISPLAY_ONLY
from engine.models import Bridge, ModelResult, TerminalLeg
from engine.presets import Preset, encode_assumption_set
from engine.reverse import ImpliedResult


def _label(name: str) -> str:
    return name.replace("_", " ").capitalize()


# ── the verdict sentence (owner spec, 2026-08-15) ───────────────────────────
# Generated here, server-side, so the wording is versioned and testable.
# Convention: dollars ≥ $20 print whole, below that 2dp; gaps whole percent;
# growth rates 1dp. The implied-growth sentence carries the qualifier "on
# this model's other assumptions" (owner decision — the implied g is
# conditional on the model's WACC and margins, not the market's own belief).

_SHORT_FIXUPS = {
    "JPMORGAN CHASE": "JPMorgan Chase",
    "MCDONALDS": "McDonald's",
    "AT&T": "AT&T",
    "IBM": "IBM",
    "3M": "3M",
    "EBAY": "eBay",
}
_NAME_SUFFIXES = {"CORP", "CORPORATION", "INC", "INCORPORATED", "CO",
                  "COMPANY", "PLC", "LTD", "LIMITED", "LP", "NV", "SA", "SE",
                  "AG"}


def short_name(filed: str) -> str:
    """Filer name → prose name: strip legal suffixes and slash-wrapped EDGAR
    markers (/DE/, /NEW). 'MICROSOFT CORP' → 'Microsoft'."""
    words = [w for w in filed.replace(",", " ").replace(".", " ").split() if w]
    while words and (words[-1] == "&" or words[-1].startswith("/")
                     or words[-1].upper() in _NAME_SUFFIXES):
        words.pop()
    if not words:
        words = filed.split()
    base = " ".join(words)
    fixed = _SHORT_FIXUPS.get(base.upper())
    if fixed:
        return fixed
    if base.isupper():
        return " ".join(w.title() for w in words)
    return base


def _dollars(v: float) -> str:
    return f"${v:,.0f}" if abs(v) >= 20 else f"${v:,.2f}"


def _pct1(x: float) -> str:
    return f"{x * 100:.1f}%"


def _gap(vs_price: float) -> str:
    return f"{abs(round(vs_price * 100)):.0f}%"


_EXIT_CLAUSE = ("There's no exit-multiple cross-check: current-year EBITDA "
                "is negative, so no market multiple applies.")
_EXIT_ANCHOR_CLAUSE = ("The exit-multiple cross-check is unavailable too: "
                       "projected terminal-year EBITDA is negative.")


def _implied_sentence(reverse: dict | None) -> str | None:
    solve = (reverse or {}).get("terminal_growth")
    if not solve:
        return None
    if solve["status"] == "solved" and solve["implied"] is not None:
        return (f"The market is pricing in {_pct1(solve['implied'])} growth "
                "forever — on this model's other assumptions.")
    if solve["status"] == "no_solution_below_wacc":
        return ("No growth rate below the discount rate closes that gap: the "
                "market is pricing something outside this model's steady "
                "state.")
    return ("Even the lowest long-run growth this model allows keeps it "
            "above the market.")


def verdict_text(company_name: str, terminal_growth: float, price: float,
                 gordon: dict, exit_multiple: dict,
                 reverse: dict | None) -> dict:
    """One or two plain sentences stating what the model concludes — a
    variant for every state the legs can be in."""
    name = short_name(company_name)
    g = _pct1(terminal_growth)
    p = _dollars(price)

    exit_clause = None
    if not exit_multiple["available"]:
        code = exit_multiple["reason"]["code"]
        exit_clause = (_EXIT_ANCHOR_CLAUSE
                       if code == "terminal_anchor_negative" else _EXIT_CLAUSE)

    if gordon["available"]:
        v = gordon["value_per_share"]
        if v <= 0:
            text = (f"At {g} long-run growth, {name}'s projected cash flows "
                    "don't cover its debt — enterprise value falls short of "
                    "net obligations, leaving nothing for shareholders. The "
                    f"market's {p} is pricing a recovery this steady-state "
                    "model doesn't attempt.")
            if exit_clause:
                text += f" {exit_clause}"
            return {"text": text, "state": "negative_equity"}
        side = "below" if gordon["vs_price"] < 0 else "above"
        text = (f"At {g} long-run growth, {name} is worth {_dollars(v)} a "
                f"share — {_gap(gordon['vs_price'])} {side} its {p} price.")
        implied = _implied_sentence(reverse)
        if implied:
            text += f" {implied}"
        if exit_clause:
            text += f" {exit_clause}"
        return {"text": text, "state": "ok"}

    # Gordon leg unavailable — the negative terminal anchor
    lead = (f"A perpetuity value isn't defined for {name}: projected cash "
            "flow is still negative in the terminal year, and a negative "
            "cash flow can't grow forever.")
    if exit_multiple["available"]:
        v = exit_multiple["value_per_share"]
        side = "below" if exit_multiple["vs_price"] < 0 else "above"
        mult = exit_multiple["tv_detail"].get("multiple")
        mult_str = f"its {mult:.1f}× exit multiple" if mult else "an exit multiple"
        text = (f"{lead} On {mult_str} the model gets {_dollars(v)} a share "
                f"— {_gap(exit_multiple['vs_price'])} {side} the {p} price.")
        return {"text": text, "state": "no_gordon"}
    text = (f"{lead} And with negative EBITDA there's no market multiple to "
            "apply either. Neither method produces a defensible number at "
            "current profitability.")
    return {"text": text, "state": "no_legs"}


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
            "editable": f.name not in DISPLAY_ONLY,
        })
    return rows


def provenance_counts(m: ModelResult) -> dict[str, int]:
    counts = {"derived": 0, "preset": 0, "user": 0}
    for f in m.assumptions.fields.values():
        p = f.provenance
        counts["preset" if p.startswith("preset") else p] += 1
    return counts


def warnings_out(m: ModelResult) -> list[dict]:
    # severity: "warn" | "info" — info = disclosure, not defect. Ingest and
    # market warnings predate the field and are all "warn".
    out = []
    for w in m.history.warnings:
        out.append({"origin": "ingest", "code": w.code, "message": w.message,
                    "fiscal_year": w.fiscal_year, "item": w.item,
                    "severity": "warn", "detail": dict(w.detail)})
    for w in m.market.warnings:
        out.append({"origin": "market", "code": w.code, "message": w.message,
                    "fiscal_year": None, "item": None, "severity": "warn",
                    "detail": dict(w.detail)})
    for w in m.warnings:
        out.append({"origin": "engine", "code": w.code, "message": w.message,
                    "fiscal_year": None, "item": None, "severity": w.severity,
                    "detail": dict(w.detail)})
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


def curves_out(m: ModelResult, reverse_dict: dict | None) -> dict:
    """Engine-computed value curves for the Summary sliders. Computed against
    the CURRENT effective assumptions (presets and edits reshape the curve).
    Landmarks are inserted as exact curve points so the thumb lands on them.
    No Gordon leg (negative terminal anchor) → no curve; the leg's own
    reason explains why the slider is absent."""
    if "gordon" not in m.bridges:
        return {}
    from engine.reverse import value_curve
    derived = m.assumptions.fields["terminal_growth"].value
    current = m.assumptions.eff("terminal_growth")
    rf = m.market.risk_free.value
    solve = (reverse_dict or {}).get("terminal_growth")
    implied = solve["implied"] if solve and solve["status"] == "solved" else None
    curve = value_curve(m.history, m.market, m.assumptions, "terminal_growth",
                        m.valuation_date,
                        extra_points=(derived, current, implied, rf))
    lo, hi = curve["domain"]
    return {"terminal_growth": {
        "leg": "gordon",
        "domain": [lo, hi],
        "points": [[x, v] for x, v in curve["points"]],
        "landmarks": {"derived": derived, "current": current,
                      "market_implied": implied,
                      "rf": rf if lo <= rf <= hi else None,
                      "block": hi},
    }}


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
    gordon = _leg(m, "gordon", price)
    exit_mult = _leg(m, "exit_multiple", price)
    reverse_dict = _reverse_out(reverse)
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
        "verdict": verdict_text(h.company.name,
                                m.assumptions.eff("terminal_growth"),
                                price, gordon, exit_mult, reverse_dict),
        "curves": curves_out(m, reverse_dict),
        "valuation": {
            "gordon": gordon,
            "exit_multiple": exit_mult,
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
        "reverse": reverse_dict,
    }


def serialize_preset(p: Preset) -> dict:
    return {
        "name": p.name, "title": p.title, "rationale": p.rationale,
        "builtin": p.builtin, "applicability": dict(p.applicability),
        "fields": [{"field": f.name, "form": f.form, "rule": f.rule,
                    "value": f.value, "solver": f.solver, "target": f.target,
                    "optional": f.optional, "note": f.note}
                   for f in p.fields.values()],
    }


def _coverage_verdict(ticker: str, detail: dict) -> str:
    share = detail.get("assets_named_share")
    pct = f"only {share:.0%} of assets tie" if share is not None else \
        "too little of the balance sheet ties"
    largest = " ".join(detail.get("largest_unattributed", []))
    financing = any(pat in largest for pat in
                    ("Financing", "FinanceReceivable", "ReceivableFinance"))
    arm = (" — most of the rest is its financing arm, a lending business "
           "this enterprise DCF can't price" if financing else "")
    return (f"{ticker} can't be modeled honestly from its filings: {pct} to "
            f"named line items{arm}. A model built on guesses would look "
            "precise and be wrong, so this tool declines.")


def refusal_verdict(code: str, exc) -> str:
    """The plain-English sentence for each designed refusal — deliberate
    rigor, not failure (owner spec). Codes stay in reason.code for Audit."""
    d = dict(exc.detail)
    t = d.get("ticker", "This company")
    if code == "financial_company":
        cat = d.get("category", "bank or financial company")
        return (f"{t} is a {cat}: deposits and float are raw material, not "
                "financing, so an enterprise DCF doesn't apply. This tool "
                "models non-financial companies only — by design.")
    if code == "insufficient_coverage":
        return _coverage_verdict(t, d)
    if code == "known_unsupported":
        return (f"{t} files its accounts under company-specific tags this "
                "tool doesn't yet read. Rather than mis-map them, it "
                "declines — a known limitation, not a data error.")
    if code == "validation_failed":
        return (f"{t}'s assembled statements don't reconcile — the cash flow "
                "doesn't tie to the balance sheet — and this tool won't "
                "value numbers that don't tie.")
    if code == "insufficient_history":
        return (f"{t} has fewer than three usable annual filings, which is "
                "not enough history to derive assumptions honestly.")
    if code == "unsupported_currency":
        return (f"{t} reports in a currency this tool doesn't yet handle.")
    if code == "missing_required_item":
        return (f"{t}'s filings are missing a line item this model can't be "
                "built without, and no documented rule covers its absence.")
    return exc.user_message


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
            "verdict": refusal_verdict(code, exc),
            "reason": {"code": code, "message": exc.user_message,
                       "detail": dict(exc.detail)}}
