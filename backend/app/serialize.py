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
from engine.dcf import FAMILIES
from engine.models import Bridge, MethodResult, ModelResult
from engine.presets import Preset, encode_assumption_set
from engine.reverse import ImpliedResult

# Plain label first, technical term second (owner spec, redesign 2026-08-15).
# The target reader knows the concepts; the conventions live behind them.
PLAIN_LABELS = {
    "revenue_growth_fy1": "Revenue growth, year 1",
    "revenue_cagr_uncapped": "Revenue CAGR, uncapped (context)",
    "cogs_pct": "Cost of revenue (% of revenue)",
    "rnd_pct": "R&D (% of revenue)",
    "sga_pct": "SG&A (% of revenue)",
    "other_opex_pct": "Other operating costs (% of revenue)",
    "unclassified_costs_pct": "Unclassified costs (% of revenue)",
    "sbc_pct": "Stock compensation (% of revenue)",
    "sbc_addback": "Add back stock comp (street FCF)",
    "dso": "Receivable days (DSO)",
    "dio": "Inventory days (DIO)",
    "dpo": "Payable days (DPO)",
    "oca_pct": "Other current assets (% of revenue)",
    "accrued_pct": "Accrued liabilities (% of revenue)",
    "ocl_pct": "Other current liabilities (% of revenue)",
    "defrev_pct": "Deferred revenue (% of revenue)",
    "da_pct_beginning_ppe": "Depreciation (% of opening PP&E)",
    "da_pct_revenue": "Depreciation (% of revenue)",
    "capex_pct": "Capex (% of revenue)",
    "effective_tax_fy1": "Cash tax rate, year 1",
    "marginal_tax": "Marginal tax rate (terminal)",
    "risk_free": "Risk-free rate (10Y)",
    "erp": "Equity risk premium (ERP)",
    "beta": "Beta (2y weekly, adjusted)",
    "beta_raw": "Beta, raw regression (context)",
    "beta_adjusted": "Blume-adjust beta",
    "embedded_debt_rate": "Embedded debt rate (interest ÷ debt)",
    "interest_income_yield": "Interest income yield",
    "coverage_ratio": "Interest coverage (EBIT ÷ interest)",
    "kd_synthetic": "Synthetic cost of debt (rating-based)",
    "terminal_growth": "Long-run growth (terminal g)",
    "terminal_growth_rf_ceiling": "10Y ceiling for long-run growth",
    "terminal_roic": "Terminal return on capital (ROIC)",
    "terminal_roic_fade": "Fade terminal ROIC toward WACC",
    "exit_multiple": "Exit multiple (EV/EBITDA)",
    "forecast_years": "Forecast horizon",
    "midyear": "Mid-year discounting",
    "payout_ratio": "Dividend payout ratio",
    "share_count": "Share count (diluted)",
    "cash_floor_pct": "Operating cash floor (% of revenue)",
    "epv_margin": "EPV margin (no-growth EBIT margin)",
}


def _label(name: str) -> str:
    return PLAIN_LABELS.get(name, name.replace("_", " ").capitalize())


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
        mult = _mdetail(exit_multiple, "multiple")
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
        key = ("preset" if p.startswith("preset")
               else "derived" if p.startswith("derived") else p)
        counts[key] += 1
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


def method_out(mr: MethodResult, price: float) -> dict:
    """One registry entry → one JSON object. Deliberately generic: no method
    ids appear here, so adding a fourth method never touches this function
    (contract-tested with a stub method)."""
    base = {"id": mr.id, "label": mr.label, "family": mr.family,
            "note": mr.note}
    if not mr.availability.available:
        # honest unavailable state, reason attached (owner rule: data, not error)
        base.update({
            "available": False,
            "reason": {"code": mr.availability.reason_code,
                       "message": mr.availability.reason, "detail": {}},
        })
        return base
    bridge: Bridge = mr.bridge
    base.update({
        "available": True,
        "value_per_share": bridge.value_per_share,
        "vs_price": bridge.value_per_share / price - 1,
        "enterprise_value": mr.enterprise_value,
        "equity_value": bridge.equity_value,
        "detail": [{"key": d.key, "label": d.label, "unit": d.unit,
                    "value": d.value} for d in mr.detail],
        "bridge": [{"name": i.name, "value": i.value, "source": i.source,
                    "note": i.note} for i in bridge.items],
    })
    return base


def _mdetail(method: dict, key: str) -> float | None:
    for d in method.get("detail", []):
        if d["key"] == key:
            return d["value"]
    return None


def growth_out(m: ModelResult) -> dict:
    """The DCF − EPV comparison, with the server-written sentences — one per
    view, since the same number reads differently from each side (owner spec
    2026-08-16). The inverted case is a labeled state, never a negative
    'value of growth'."""
    g = m.growth
    if not g.available:
        return {"available": False, "state": "unavailable",
                "reason": {"code": g.reason_code, "message": g.reason},
                "text": g.reason, "epv_text": g.reason}
    if g.state == "value_destructive":
        text = ("Earnings power alone is worth more than the DCF: the "
                "projected path earns less than holding today's profits "
                "flat, so growth at these assumptions destroys value — "
                "returns below the cost of capital, or shrinkage.")
        epv_text = ("The DCF view comes out BELOW this number: the projected "
                    "path is worth less than holding today's profits flat — "
                    "at these assumptions, growth destroys value.")
    else:
        share = (f" — {g.share_of_dcf:.0%} of the DCF value rests on growth"
                 f" beyond today's earnings power" if g.share_of_dcf is not None
                 else "")
        text = f"Growth is worth {_dollars(g.per_share)} a share here{share}."
        epv_text = (f"The DCF view prices growth at {_dollars(g.per_share)} "
                    "a share on top of this no-growth value.")
    return {"available": True, "state": g.state, "per_share": g.per_share,
            "share_of_dcf": g.share_of_dcf, "text": text,
            "epv_text": epv_text}


def epv_verdict_text(company_name: str, epv: dict, price: float) -> dict:
    """The EPV view's own verdict sentence — one variant per state the
    method can be in, written here so it is versioned and testable."""
    name = short_name(company_name)
    p = _dollars(price)
    if not epv["available"]:
        return {"text": (f"A no-growth value isn't defined for {name}: its "
                         "normalized operating margin is negative, so there "
                         "is no earnings power to capitalize. The DCF view — "
                         "which can price a projected recovery — remains "
                         "available."),
                "state": "no_epv"}
    v = epv["value_per_share"]
    if v <= 0:
        return {"text": (f"Today's earnings power doesn't cover {name}'s net "
                         "obligations — at zero growth, enterprise value "
                         "falls short of debt, leaving nothing for "
                         f"shareholders. The market's {p} is pricing growth "
                         "this view deliberately excludes."),
                "state": "negative_equity"}
    side = "below" if epv["vs_price"] < 0 else "above"
    return {"text": (f"On today's demonstrated earnings power alone — no "
                     f"growth — {name} is worth {_dollars(v)} a share, "
                     f"{_gap(epv['vs_price'])} {side} its {p} price."),
            "state": "ok"}


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


# ── warnings digest (Summary tab) ────────────────────────────────────────────
# Warnings rewritten as sentences and grouped; nothing dropped — every entry
# carries its code(s) and count so the Audit tab can expand it. coverage_low
# never folds and always leads.


def _yrs(group: list[dict]) -> list[int]:
    return sorted({w["fiscal_year"] for w in group
                   if w.get("fiscal_year") is not None})


def _digest_unmapped(group: list[dict], company: str) -> str:
    n = len(group)
    if n == 1:
        w = group[0]
        return (f"{company} doesn't report {w.get('item') or 'one minor item'}"
                " as its own line; it was carried at zero, with a note.")
    years = _yrs(group)
    span = f" across {len(years)} fiscal years" if len(years) > 1 else ""
    return (f"{company} doesn't report every minor line item separately; "
            f"{n} optional lines{span} were carried at zero, each noted.")


def _digest_restated(group: list[dict], company: str) -> str:
    n = len(group)
    figure = "One prior-period figure" if n == 1 else \
        f"{n} prior-period figures"
    return (f"{figure} changed in later filings; the restated numbers are "
            "used — the right basis for forecasting.")


def _digest_week53(group: list[dict], company: str) -> str:
    years = ", ".join(f"FY{y}" for y in _yrs(group)) or "One year"
    return (f"{years} ran 53 weeks, so growth against adjacent years is "
            "inflated by roughly 1.9%.")


def _digest_share_derived(group: list[dict], company: str) -> str:
    return (f"Share counts are derived as net income ÷ EPS because "
            f"{company} reports share data by class; the derivation is "
            "disclosed on every per-share figure.")


def _digest_interest_imputed(group: list[dict], company: str) -> str:
    return (f"{company} reports debt but no interest expense line, so "
            "interest is imputed at the synthetic cost of debt rather than "
            "silently zero.")


def _digest_split(group: list[dict], company: str) -> str:
    return ("Share and per-share history is recast for stock splits — "
            "labeled as splits, not restatements.")


def _digest_residual(group: list[dict], company: str) -> str:
    n = len(group)
    years = "one year" if n == 1 else f"{n} years"
    return (f"The cash flow statement leaves a small unreconciled residual "
            f"in {years} — below the materiality bar (1% of revenue, 5% of "
            "gross flows), quantified per year.")


_DIGEST_TEMPLATES = {
    "unmapped_item": _digest_unmapped,
    "restated": _digest_restated,
    "week53": _digest_week53,
    "share_count_derived": _digest_share_derived,
    "interest_imputed": _digest_interest_imputed,
    "split_adjustment": _digest_split,
    "immaterial_cash_residual": _digest_residual,
}


def warnings_digest(warnings: list[dict], company: str) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for w in warnings:
        groups.setdefault((w["code"], w["severity"]), []).append(w)

    entries = []
    for (code, severity), group in groups.items():
        hard = code == "coverage_low"
        template = _DIGEST_TEMPLATES.get(code)
        if template:
            text = template(group, company)
        else:
            text = group[0]["message"]
            if len(group) > 1:
                text += f" (+{len(group) - 1} more like this)"
        entries.append({"text": text, "codes": [code], "count": len(group),
                        "severity": severity, "hard": hard})
    # hard first, then warnings by weight, then notes
    entries.sort(key=lambda e: (not e["hard"], e["severity"] == "info",
                                -e["count"]))
    return entries


# direction cues for the headline drivers — why the arrow points that way
_DRIVER_NOTES = {
    "wacc": "set by beta, ERP and the 10Y · future cash is worth less today",
    "terminal_growth": "compounds forever in the terminal value",
    "revenue_growth_fy1": "sets the base every later year compounds from",
    "cogs_pct": "every point of margin flows to free cash flow",
    "rnd_pct": "every point of margin flows to free cash flow",
    "sga_pct": "every point of margin flows to free cash flow",
    "other_opex_pct": "every point of margin flows to free cash flow",
    "unclassified_costs_pct": "every point of margin flows to free cash flow",
    "sbc_pct": "expensed in FCF here — no add-back by default",
    "effective_tax_fy1": "taken straight out of every year's cash flow",
    "marginal_tax": "taxes the terminal year, where most value sits",
    "capex_pct": "cash out the door before free cash flow",
    "terminal_roic": "sets what perpetual growth costs in reinvestment",
    "exit_multiple": "prices the terminal year the way the market prices today",
    "dso": "working capital tied up as the business grows",
    "dio": "working capital tied up as the business grows",
    "dpo": "supplier financing released as the business grows",
}
_STEP_LABELS = {"rate": "±1pp", "ratio": "±1pp", "x": "±0.5×",
                "days": "±5 days", "wacc": "±1pp"}


def drivers_out(m: ModelResult) -> list[dict]:
    """Top-5 headline drivers, ranked by engine-computed impact on the
    headline leg (methodology: driver_ranking). Empty when no leg values."""
    from engine.drivers import driver_impacts
    leg = ("gordon" if "gordon" in m.bridges
           else "exit_multiple" if "exit_multiple" in m.bridges else None)
    if leg is None:
        return []
    return [{
        "name": d.name,
        "label": ("Discount rate (WACC)" if d.name == "wacc"
                  else _label(d.name)),
        "direction": "up" if d.direction > 0 else "down",
        "step_label": _STEP_LABELS[d.unit],
        "impact_per_share": d.impact_per_share,
        "note": _DRIVER_NOTES.get(d.name, ""),
        "composite": d.composite,
        "leg": leg,
    } for d in driver_impacts(m.history, m.market, m.assumptions,
                              m.valuation_date, leg)]


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


def profile_out(m: ModelResult) -> dict | None:
    """The company profile with its measured trigger values — disclosed,
    never silent (owner spec). None when the profile layer was skipped."""
    prof = getattr(m.assumptions, "profile", None)
    if prof is None:
        return None
    mm = prof.measures
    return {
        "tag": prof.tag,
        "primary": prof.primary,
        "modifiers": list(prof.modifiers),
        "reassigned": prof.reassigned,
        "notes": list(prof.notes),
        "measures": {
            "cagr": mm.cagr, "g_latest": mm.g_latest,
            "roic_median": mm.roic_median,
            "roic_years_above_wacc": mm.roic_years_above_wacc,
            "roic_years": mm.roic_years, "wacc": mm.wacc,
            "margin_range": mm.margin_range,
            "rev_down_years": mm.rev_down_years,
            "capex_da": mm.capex_da, "window": mm.window,
        },
    }


def serialize_model(m: ModelResult, preset: Preset | None,
                    overrides: dict | None,
                    reverse: dict[str, ImpliedResult] | None,
                    profile: str | None = None) -> dict:
    price = m.market.price.value
    h = m.history
    cov = h.coverage
    methods = [method_out(mr, price)
               for mr in sorted(m.methods, key=lambda mr: mr.order)]
    by_id = {mo["id"]: mo for mo in methods}
    gordon, exit_mult = by_id["gordon"], by_id["exit_multiple"]
    reverse_dict = _reverse_out(reverse)
    warnings = warnings_out(m)
    return {
        "status": "ok",
        "ticker": m.ticker,
        "valuation_date": m.valuation_date.isoformat(),
        "code": encode_assumption_set(
            preset.name if preset else None, overrides or None, profile),
        "profile": profile_out(m),
        "company": {
            "name": h.company.name,
            "short_name": short_name(h.company.name),
            "cik": h.company.cik,
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
        "drivers": drivers_out(m),
        "valuation": methods,
        "families": [dict(f) for f in FAMILIES],
        "growth": growth_out(m),
        "epv_verdict": epv_verdict_text(h.company.name, by_id["epv"], price),
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
        "warnings": warnings,
        "warnings_digest": warnings_digest(warnings,
                                           short_name(h.company.name)),
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
