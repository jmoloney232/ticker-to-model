"""Reverse DCF: what is the market price implying? (owner-requested; a
diagnostic first, a UI feature later.)

Holding every other default fixed, solve for the single assumption value that
makes the Gordon leg equal a target price. The comparison then reads as
"we assume 25.3% capex; the market implies 14.1%" — the market price is a
comparison point, never ground truth for intrinsic value.

Pure functions (engine discipline). Bisection, because value is monotonic in
each solved field over sensible ranges and bisection cannot invent precision:
where no bracket exists in range, the honest answer is "no solution", and
terminal-growth solutions at or above WACC are reported as
no_solution_below_wacc — never as a number.

Structural property worth knowing before reading results: under the Gordon
leg with reinvestment consistency, the terminal value is NOPAT-driven
(RR = g/ROIC governs perpetual reinvestment), so explicit-window capex only
moves the five projected years. Its reach is therefore bounded: when the
model-vs-price gap exceeds what the explicit window can bridge, implied capex
reports no_solution_in_range — which is itself a finding (the disagreement
lives in the terminal economics, not the near-term spend).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date

from ingest.models import FinancialHistory
from market.models import MarketInputs

from .assumptions import derive_assumptions
from .dcf import _resolve_roic, _stub, build_bridge, terminal_gordon, ufcf_schedule
from .errors import InvalidAssumptionError
from .models import Assumptions
from .projections import project
from .wacc import build_wacc

MAX_ITERATIONS = 80
PRICE_TOL_REL = 1e-7

FIELDS = ("terminal_growth", "capex_pct", "revenue_growth_fy1", "ebitda_margin")


@dataclass(frozen=True)
class ImpliedResult:
    field: str
    derived: float | None          # the model's default (comparison anchor)
    implied: float | None          # what makes Gordon == target; None if none
    status: str                    # solved | no_solution_below_wacc |
    #                                no_solution_in_range
    lo: float
    hi: float
    target_price: float


def _gordon_per_share(history: FinancialHistory, assumptions: Assumptions,
                      wacc: float, stub: float) -> float:
    """Gordon-leg value per share without the sensitivity grids — the hot path
    of the solver (bisection re-values ~50× per field)."""
    projections = project(history, assumptions)
    g = assumptions.eff("terminal_growth")
    roic = _resolve_roic(assumptions, g, wacc, warnings=None)
    schedule = ufcf_schedule(projections, assumptions, wacc, stub)
    leg = terminal_gordon(projections, assumptions, wacc, g, roic, stub)
    ev = sum(y.pv for y in schedule) + leg.pv
    return build_bridge(history, assumptions, "gordon", ev).value_per_share


def _ebit_margin(assumptions: Assumptions) -> float:
    total = sum(assumptions.fields[k].value
                for k in ("rnd_pct", "sga_pct", "other_opex_pct",
                          "unclassified_costs_pct"))
    if assumptions.cost_structure == "by_function":
        total += assumptions.fields["cogs_pct"].value
    return 1.0 - total


def _apply(assumptions: Assumptions, field: str, x: float,
           da1_ratio: float) -> None:
    """Apply candidate x in place. ebitda_margin (FY1 basis) translates to a
    uniform shift of the cost stack via other_opex_pct — the memo D&A line is
    independent of cost ratios, so an EBITDA-margin shift and an EBIT-margin
    shift are the same lever, offset by FY1 D&A/revenue."""
    if field == "ebitda_margin":
        target_ebit_margin = x - da1_ratio
        shift = target_ebit_margin - _ebit_margin(assumptions)
        assumptions.fields["other_opex_pct"].override = (
            assumptions.fields["other_opex_pct"].value - shift)
    else:
        assumptions.fields[field].override = x


def implied_assumption(history: FinancialHistory, market: MarketInputs,
                       field: str, valuation_date: date,
                       target_price: float | None = None) -> ImpliedResult:
    """Solve one field so the Gordon leg equals target_price (default: the
    current market price). All other defaults stay at their derived values."""
    if field not in FIELDS:
        raise ValueError(f"unsupported reverse-DCF field {field!r} "
                         f"(supported: {', '.join(FIELDS)})")
    target = market.price.value if target_price is None else target_price
    base = derive_assumptions(history, market)
    wacc = build_wacc(history, market, base).wacc
    stub = _stub(valuation_date, history.periods[-1].end)
    fy1 = project(history, base)[0]
    da1_ratio = fy1.cashflow["d_and_a"] / fy1.income["revenue"]

    if field == "terminal_growth":
        lo, hi = -0.02, wacc - 0.0025
        derived = base.eff("terminal_growth")
    elif field == "capex_pct":
        lo, hi = 0.0, 0.60
        derived = base.eff("capex_pct")
    elif field == "revenue_growth_fy1":
        lo, hi = -0.50, 1.00
        derived = base.eff("revenue_growth_fy1")
    else:                                  # ebitda_margin, FY1 basis
        lo, hi = 0.01 + da1_ratio, 0.80 + da1_ratio     # EBIT margin 1–80%
        derived = _ebit_margin(base) + da1_ratio

    def f(x: float) -> float:
        candidate = copy.deepcopy(base)
        _apply(candidate, field, x, da1_ratio)
        return _gordon_per_share(history, candidate, wacc, stub) - target

    f_lo, f_hi = f(lo), f(hi)
    if f_lo == 0.0 or f_hi == 0.0:
        return ImpliedResult(field, derived, lo if f_lo == 0.0 else hi,
                             "solved", lo, hi, target)
    if f_lo * f_hi > 0:
        status = ("no_solution_below_wacc"
                  if field == "terminal_growth" and f_hi < 0
                  else "no_solution_in_range")
        return ImpliedResult(field, derived, None, status, lo, hi, target)

    a, b, fa = lo, hi, f_lo
    for _ in range(MAX_ITERATIONS):
        mid = (a + b) / 2
        fm = f(mid)
        if abs(fm) <= PRICE_TOL_REL * target:
            return ImpliedResult(field, derived, mid, "solved", lo, hi, target)
        if fa * fm < 0:
            b = mid
        else:
            a, fa = mid, fm
    return ImpliedResult(field, derived, (a + b) / 2, "solved", lo, hi, target)


def implied_all(history: FinancialHistory, market: MarketInputs,
                valuation_date: date) -> dict[str, ImpliedResult]:
    return {field: implied_assumption(history, market, field, valuation_date)
            for field in FIELDS}


# ── value curves (slider mechanism, owner spec 2026-08-15) ───────────────────
# The frontend calculates nothing: a slider reads engine-computed
# (x, per-share) points and snaps to them; the authoritative recompute fires
# on release. General by assumption name — more sliders later.

CURVE_POINTS = 25


def value_curve(history: FinancialHistory, market: MarketInputs,
                assumptions: Assumptions, field: str, valuation_date: date,
                extra_points: tuple[float, ...] = ()) -> dict:
    """(x, Gordon per-share) across `field`'s valid domain, holding every
    other EFFECTIVE assumption fixed — presets and user overrides reshape the
    curve. `extra_points` (landmarks: the derived default, the market-implied
    value, the risk-free rate) are inserted exactly so the slider thumb can
    land on them. A point where the engine cannot value is None, never a
    guess."""
    if field not in FIELDS:
        raise ValueError(f"unsupported curve field {field!r} "
                         f"(supported: {', '.join(FIELDS)})")
    wacc = build_wacc(history, market, assumptions).wacc
    stub = _stub(valuation_date, history.periods[-1].end)
    fy1 = project(history, assumptions)[0]
    da1_ratio = fy1.cashflow["d_and_a"] / fy1.income["revenue"]

    if field == "terminal_growth":
        lo, hi = -0.02, wacc - 0.0025
    elif field == "capex_pct":
        lo, hi = 0.0, 0.60
    elif field == "revenue_growth_fy1":
        lo, hi = -0.50, 1.00
    else:                                  # ebitda_margin, FY1 basis
        lo, hi = 0.01 + da1_ratio, 0.80 + da1_ratio

    step = (hi - lo) / (CURVE_POINTS - 1)
    xs = {lo + i * step for i in range(CURVE_POINTS)}
    xs.update(x for x in extra_points if x is not None and lo <= x <= hi)

    points: list[tuple[float, float | None]] = []
    for x in sorted(xs):
        candidate = copy.deepcopy(assumptions)
        _apply(candidate, field, x, da1_ratio)
        try:
            v = _gordon_per_share(history, candidate, wacc, stub)
        except (ValueError, ZeroDivisionError, InvalidAssumptionError):
            v = None                       # honest gap, not an interpolation
        points.append((x, v))
    return {"field": field, "domain": (lo, hi), "points": points}
