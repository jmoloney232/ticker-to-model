"""Headline-driver ranking (owner spec, frontend redesign 2026-08-15).

The Summary tab's "what moves this valuation" list is ranked by ACTUAL
impact for the company at hand, never a fixed list: impact = mean absolute
per-share move of the headline leg under a standardized ± step of each
editable assumption. Steps are uniform by unit (transparency over realism —
the historically-scaled alternative ranks "realistically" but is opaque;
owner chose uniform, documented in methodology.yaml `driver_ranking`, and
the step prints beside each driver).

WACC appears as a composite driver — the concept the target reader thinks
in — swept directly like the sensitivity-grid rows; its inputs (beta, ERP,
risk-free, cost-of-debt inputs) are excluded individually so one concept
isn't double-listed.

Pure functions (engine discipline): no I/O, dataclasses in and out.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date

from ingest.models import FinancialHistory
from market.models import MarketInputs

from .assumptions import DISPLAY_ONLY
from .dcf import _stub, build_bridge, terminal_exit, ufcf_schedule
from .errors import InvalidAssumptionError
from .models import Assumptions
from .projections import project
from .reverse import _gordon_per_share
from .wacc import build_wacc

# steps by unit — parity-tested against methodology.yaml driver_ranking
STEPS = {"rate": 0.01, "ratio": 0.01, "x": 0.5, "days": 5.0}
WACC_STEP = 0.01

# WACC inputs (composite covers them) and non-drivers; epv_margin drives
# the EPV method only — it cannot move the headline leg, so sweeping it
# would print a zero-impact row
EXCLUDED = frozenset({
    "beta", "beta_raw", "erp", "risk_free", "embedded_debt_rate",
    "coverage_ratio", "interest_income_yield",
    "share_count", "forecast_years", "epv_margin",
}) | DISPLAY_ONLY

TOP_N = 5


@dataclass(frozen=True)
class DriverImpact:
    name: str                    # assumption name, or "wacc" (composite)
    impact_per_share: float      # mean |Δ per-share| for the ± step
    direction: int               # sign of Δvalue for +step (+1 / −1)
    step: float                  # the standardized step, engine units
    unit: str                    # "rate" | "ratio" | "x" | "days" | "wacc"
    composite: bool              # True for WACC (not directly editable)


def _exit_per_share(history: FinancialHistory, assumptions: Assumptions,
                    wacc: float, stub: float) -> float:
    projections = project(history, assumptions)
    schedule = ufcf_schedule(projections, assumptions, wacc, stub)
    leg = terminal_exit(projections, assumptions.eff("exit_multiple"),
                        wacc, stub)
    ev = sum(y.pv for y in schedule) + leg.pv
    return build_bridge(history, assumptions, "exit_multiple",
                        ev).value_per_share


def driver_impacts(history: FinancialHistory, market: MarketInputs,
                   assumptions: Assumptions, valuation_date: date,
                   leg: str = "gordon") -> list[DriverImpact]:
    """Ranked impacts on the headline leg's per-share value, largest first.
    Candidates: every editable numeric assumption not excluded above, plus
    the WACC composite. A step that leaves the valid domain is clamped to a
    one-sided move; a candidate the engine can't value on either side is
    skipped, never guessed."""
    wacc = build_wacc(history, market, assumptions).wacc
    stub = _stub(valuation_date, history.periods[-1].end)
    value = _gordon_per_share if leg == "gordon" else _exit_per_share

    def value_at(candidate: Assumptions, w: float) -> float | None:
        # a probe the engine rejects (domain constraint, degenerate math) is
        # a skipped side, never a guess
        try:
            return value(history, candidate, w, stub)
        except (ValueError, ZeroDivisionError, InvalidAssumptionError):
            return None

    base = value_at(assumptions, wacc)
    if base is None:
        return []

    out: list[DriverImpact] = []

    # WACC composite — same sweep convention as the sensitivity-grid rows
    up = value_at(assumptions, wacc + WACC_STEP)
    down = value_at(assumptions, wacc - WACC_STEP)
    if up is not None and down is not None:
        impact = (abs(up - base) + abs(down - base)) / 2
        out.append(DriverImpact("wacc", impact, 1 if up > base else -1,
                                WACC_STEP, "wacc", True))

    g_cap = wacc - 0.0025                  # terminal-growth block edge
    for f in assumptions.fields.values():
        if (f.name in EXCLUDED or f.unit not in STEPS
                or not isinstance(f.effective, (int, float))
                or isinstance(f.effective, bool)):
            continue
        step = STEPS[f.unit]
        x0 = float(f.effective)
        hi_x, lo_x = x0 + step, x0 - step
        if f.name == "terminal_growth":
            hi_x = min(hi_x, g_cap)
            lo_x = max(lo_x, -0.02)

        deltas = []
        direction = 0
        for x in (hi_x, lo_x):
            if x == x0:
                continue
            candidate = copy.deepcopy(assumptions)
            candidate.fields[f.name].override = x
            v = value_at(candidate, wacc)
            if v is None:
                continue
            deltas.append(abs(v - base))
            if x > x0 and direction == 0:
                direction = 1 if v >= base else -1
            elif x < x0 and direction == 0:
                direction = 1 if v <= base else -1
        if not deltas:
            continue
        out.append(DriverImpact(f.name, sum(deltas) / len(deltas),
                                direction, step, f.unit, False))

    out.sort(key=lambda d: (-d.impact_per_share, d.name))
    return out[:TOP_N]
