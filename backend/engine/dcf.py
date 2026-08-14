"""DCF: discounting, both terminal values, EV→equity bridge, sensitivity
grids, and the engine's single entry point `build_model` (specs/04-engine.md).

Pure and deterministic: ValuationDate is an input, never a clock read.
"""

from __future__ import annotations

from datetime import date

from ingest.models import CheckResult, FinancialHistory, ValidationReport
from market.models import MarketInputs

from .assumptions import apply_overrides, derive_assumptions, gross_debt
from .errors import InvalidAssumptionError
from .models import (
    Assumptions,
    Bridge,
    BridgeItem,
    EngineWarning,
    ModelResult,
    ProjectedPeriod,
    SensitivityGrid,
    TerminalLeg,
    UFCFYear,
    WaccBuild,
)
from .projections import project, tax_path
from .wacc import build_wacc

TV_SHARE_INFO = 0.85          # P8: value is mostly terminal
LEASE_HEAVY = 0.25            # P6: operating leases vs gross debt
WACC_STEP, G_STEP, MULT_STEP = 0.005, 0.005, 1.0
GRID_OFFSETS = (-2, -1, 0, 1, 2)


def _stub(vd: date, fy0_end: date) -> float:
    """Elapsed fraction of a year since FYE_0. Fiscal years are integer-spaced
    from FYE_0 (t_i = i − stub): mathematically the spec's day-count formula,
    minus leap-day jitter — so vd = FYE_0 with mid-year off reproduces the
    textbook year-end DCF EXACTLY (tested invariant). vd past FYE_1 goes
    negative honestly and draws the history_stale warning."""
    return (vd - fy0_end).days / 365.25


def ufcf_schedule(projections: list[ProjectedPeriod], assumptions: Assumptions,
                  wacc: float, stub: float) -> list[UFCFYear]:
    taxes = tax_path(assumptions)
    addback_on = bool(assumptions.eff("sbc_addback"))
    midyear = bool(assumptions.eff("midyear"))
    out = []
    for i, p in enumerate(projections):
        ebit = p.income["operating_income"]
        nopat = ebit * (1 - taxes[i])
        da = p.cashflow["d_and_a"]
        sbc_addback = p.cashflow["stock_compensation"] if addback_on else 0.0
        capex = p.cashflow["capex"]
        delta_nwc = -p.cashflow["working_capital_change"]
        ufcf = nopat + da + sbc_addback - capex - delta_nwc
        t = (i + 1) - stub - (0.5 if midyear else 0.0)
        df = (1 + wacc) ** (-t)
        out.append(UFCFYear(fiscal_year=p.fiscal_year, ebit=ebit, tax_rate=taxes[i],
                            nopat=nopat, d_and_a=da, sbc_addback=sbc_addback,
                            capex=capex, delta_nwc=delta_nwc, ufcf=ufcf,
                            exponent=t, discount_factor=df, pv=ufcf * df))
    return out


def _resolve_roic(assumptions: Assumptions, g: float, wacc: float,
                  warnings: list[EngineWarning] | None) -> float:
    """Owner decision: degenerate ROIC falls back to WACC — value-neutral
    reinvestment. An explicit user override that violates RR < 1 is rejected
    instead (their input, their constraint)."""
    field = assumptions.fields["terminal_roic"]
    if field.override is not None:
        if field.override <= g:
            raise InvalidAssumptionError("terminal_roic",
                                         "must exceed terminal g (RR = g/ROIC < 1)",
                                         field.override)
        return field.override
    if field.value is None or field.value <= g:
        if warnings is not None:
            warnings.append(EngineWarning(
                code="roic_fallback",
                message=("Terminal ROIC set to WACC: terminal reinvestment is "
                         "value-neutral because returns could not be estimated "
                         "from history (invested capital ≤ 0 or ROIC ≤ g)."),
                detail={"derived_roic": field.value}))
        return wacc
    return field.value


def terminal_gordon(projections: list[ProjectedPeriod], assumptions: Assumptions,
                    wacc: float, g: float, roic: float, stub: float) -> TerminalLeg:
    """Gordon with reinvestment consistency: RR = g/ROIC_t. Mid-year on
    discounts at t_N − 0.5 (perpetual flows arrive through each year)."""
    fy5 = projections[-1]
    marginal = assumptions.eff("marginal_tax")
    nopat_n1 = fy5.income["operating_income"] * (1 + g) * (1 - marginal)
    rr = g / roic
    fcf_terminal = nopat_n1 * (1 - rr)
    tv = fcf_terminal / (wacc - g)
    t_n = len(projections) - stub
    exponent = t_n - (0.5 if assumptions.eff("midyear") else 0.0)
    return TerminalLeg(method="gordon", value_at_fyeN=tv, exponent=exponent,
                       pv=tv * (1 + wacc) ** (-exponent),
                       detail={"nopat_n1": nopat_n1, "reinvestment_rate": rr,
                               "fcf_terminal": fcf_terminal, "g": g, "roic": roic})


def terminal_exit(projections: list[ProjectedPeriod], multiple: float,
                  wacc: float, stub: float) -> TerminalLeg:
    """Exit multiple discounts at FULL t_N regardless of the mid-year toggle —
    a sale is a point-in-time year-end event (deliberate asymmetry, tested)."""
    fy5 = projections[-1]
    ebitda_n = fy5.income["operating_income"] + fy5.cashflow["d_and_a"]
    tv = multiple * ebitda_n
    t_n = len(projections) - stub
    return TerminalLeg(method="exit_multiple", value_at_fyeN=tv, exponent=t_n,
                       pv=tv * (1 + wacc) ** (-t_n),
                       detail={"multiple": multiple, "ebitda_n": ebitda_n})


def build_bridge(history: FinancialHistory, assumptions: Assumptions,
                 method: str, ev: float) -> Bridge:
    """EV → equity. Net debt lives HERE and only here (P3). zero_logged
    components carry their source so '0 — unmapped' never renders as a bare 0."""
    fy0 = history.periods[-1]
    a = assumptions
    marginal = a.eff("marginal_tax")

    def src(item: str) -> str:
        f = fy0.balance.get(item)
        return f.source if f is not None else "zero_logged"

    cash = fy0.value("cash_and_equivalents", 0.0)
    sti = fy0.value("short_term_investments", 0.0)
    floor = a.eff("cash_floor_pct") * fy0.value("revenue")
    excess = max(0.0, cash + sti - floor)
    pension = fy0.value("pension_liability", 0.0)
    items = [
        BridgeItem("excess_cash", excess, "computed",
                   f"cash + ST investments above the {a.eff('cash_floor_pct'):.0%}"
                   "-of-revenue operating floor"),
        BridgeItem("long_term_investments", fy0.value("long_term_investments", 0.0),
                   src("long_term_investments"), "book, non-operating"),
        BridgeItem("gross_debt", -gross_debt(fy0), "computed",
                   "ST + LT debt incl. finance leases (gross here; weights too — "
                   "net debt appears nowhere)"),
        BridgeItem("noncontrolling_interest",
                   -fy0.value("noncontrolling_interest", 0.0),
                   src("noncontrolling_interest"), "book value proxy"),
        BridgeItem("preferred_equity", -fy0.value("preferred_equity", 0.0),
                   src("preferred_equity")),
        BridgeItem("temporary_equity", -fy0.value("temporary_equity", 0.0),
                   src("temporary_equity"),
                   "redeemable NCI — a claim senior to common"),
        BridgeItem("pension_after_tax", -pension * (1 - marginal),
                   src("pension_liability"),
                   f"unfunded pension × (1 − {marginal:.0%}) — contributions are "
                   "deductible; usually unmapped (see warnings), never silently zero"),
    ]
    equity = ev + sum(i.value for i in items)
    shares = a.eff("share_count")
    return Bridge(method=method, enterprise_value=ev, items=items,
                  equity_value=equity, shares=shares,
                  value_per_share=equity / shares)


def _value_per_share(history: FinancialHistory, assumptions: Assumptions,
                     projections: list[ProjectedPeriod], stub: float, wacc: float,
                     g: float | None, multiple: float | None) -> float | None:
    """One sensitivity cell: full re-valuation at (wacc, g) or (wacc, multiple).
    Varying g re-projects (the growth path fades INTO g — spec: only the varied
    inputs change, but everything downstream of them recomputes)."""
    if g is not None:
        if g >= wacc - 1e-9:
            return None
        saved = assumptions.fields["terminal_growth"].override
        assumptions.fields["terminal_growth"].override = g
        try:
            proj = project(history, assumptions)
            roic = _resolve_roic(assumptions, g, wacc, warnings=None)
            schedule = ufcf_schedule(proj, assumptions, wacc, stub)
            tv = terminal_gordon(proj, assumptions, wacc, g, roic, stub)
        finally:
            assumptions.fields["terminal_growth"].override = saved
    else:
        proj = projections
        schedule = ufcf_schedule(proj, assumptions, wacc, stub)
        tv = terminal_exit(proj, multiple, wacc, stub)
    ev = sum(y.pv for y in schedule) + tv.pv
    return build_bridge(history, assumptions, "cell", ev).value_per_share


def sensitivity_grids(history: FinancialHistory, assumptions: Assumptions,
                      projections: list[ProjectedPeriod], stub: float,
                      base_wacc: float) -> dict[str, SensitivityGrid]:
    g0 = assumptions.eff("terminal_growth")
    waccs = [base_wacc + o * WACC_STEP for o in GRID_OFFSETS]
    gs = [g0 + o * G_STEP for o in GRID_OFFSETS]
    grids = {"wacc_x_g": SensitivityGrid(
        row_label="WACC", col_label="terminal g", rows=waccs, cols=gs,
        cells=[[_value_per_share(history, assumptions, projections, stub, w,
                                 g=g, multiple=None)
                for g in gs] for w in waccs])}
    m0 = assumptions.eff("exit_multiple")
    if m0 is not None:
        mults = [m0 + o * MULT_STEP for o in GRID_OFFSETS]
        grids["wacc_x_multiple"] = SensitivityGrid(
            row_label="WACC", col_label="exit EV/EBITDA", rows=waccs, cols=mults,
            cells=[[_value_per_share(history, assumptions, projections, stub, w,
                                     g=None, multiple=m)
                    for m in mults] for w in waccs])
    return grids


def _checks(projections: list[ProjectedPeriod], history: FinancialHistory,
            wacc_build: WaccBuild, assumptions: Assumptions,
            terminal: dict[str, TerminalLeg], ev_gordon: float,
            warnings: list[EngineWarning]) -> ValidationReport:
    fy0 = history.periods[-1]
    a = assumptions

    p1 = max(abs(p.balance["total_assets"]
                 - (p.balance["total_liabilities"] + p.balance["stockholders_equity"]
                    + p.balance["noncontrolling_interest"]
                    + p.balance["preferred_equity"] + p.balance["temporary_equity"]))
             for p in projections)
    p2 = 0.0
    prev_cash = fy0.value("cash_and_equivalents", 0.0)
    for p in projections:
        flows = p.cashflow["net_change_in_cash"]
        p2 = max(p2, abs(flows - (p.balance["cash_and_equivalents"] - prev_cash)))
        prev_cash = p.balance["cash_and_equivalents"]

    expected_gross = gross_debt(fy0)
    p3_ok = abs(wacc_build.gross_debt - expected_gross) < 1e-6

    g = a.eff("terminal_growth")
    p4_ok = (g < wacc_build.wacc and wacc_build.wacc > 0
             and a.eff("share_count") > 0)

    ceiling = min(0.025, a.eff("risk_free"))
    p5_warn = (a.fields["terminal_growth"].override is not None
               and a.fields["terminal_growth"].override > ceiling)

    leases = fy0.value("operating_lease_liability", 0.0)
    p6_warn = leases > LEASE_HEAVY * expected_gross if expected_gross > 0 else leases > 0

    p7_codes = [w.code for w in warnings
                if w.code in ("cash_plug_negative", "negative_ufcf")]
    if wacc_build.beta_source == "fallback_1.0":
        p7_codes.append("beta_fallback")

    tv_share = terminal["gordon"].pv / ev_gordon if ev_gordon > 0 else 0.0

    rel = 1e-8 * max(1.0, fy0.value("total_assets"))
    return ValidationReport(results=[
        CheckResult("P1", "fail", "pass" if p1 <= rel else "fail", magnitude=p1,
                    detail="Projected balance sheet balances exactly every year "
                           "(plug construction, still asserted)"),
        CheckResult("P2", "fail", "pass" if p2 <= rel else "fail", magnitude=p2,
                    detail="Projected cash flow ties to Δcash exactly every year"),
        CheckResult("P3", "fail", "pass" if p3_ok else "fail",
                    detail="Gross debt in WACC weights; net debt only in the bridge"),
        CheckResult("P4", "fail", "pass" if p4_ok else "fail",
                    detail=f"g={g:.2%} < WACC={wacc_build.wacc:.2%}; WACC > 0; "
                           "shares > 0; RR < 1"),
        CheckResult("P5", "warn", "warn" if p5_warn else "pass",
                    detail="Terminal-g override above min(2.5%, 10Y)" if p5_warn
                    else "Terminal g within the default ceiling"),
        CheckResult("P6", "warn", "warn" if p6_warn else "pass",
                    magnitude=leases,
                    detail=("Operating lease liability exceeds 25% of gross debt — "
                            "lease-exclusion convention materially binds (lease_heavy)"
                            if p6_warn else "Operating leases within bounds")),
        CheckResult("P7", "warn", "warn" if p7_codes else "pass",
                    detail=("Model-quality warnings: " + ", ".join(p7_codes))
                    if p7_codes else "No beta fallback, negative UFCF, or "
                                     "negative cash plug"),
        CheckResult("P8", "info", "warn" if tv_share > TV_SHARE_INFO else "pass",
                    magnitude=tv_share,
                    detail=f"Terminal value = {tv_share:.0%} of enterprise value"
                           + (" — value is mostly terminal; the explicit forecast "
                              "barely matters" if tv_share > TV_SHARE_INFO else "")),
    ])


def build_model(history: FinancialHistory, market: MarketInputs,
                valuation_date: date, overrides: dict | None = None,
                assumptions: Assumptions | None = None) -> ModelResult:
    a = assumptions or derive_assumptions(history, market)
    if overrides:
        a = apply_overrides(a, overrides)
    warnings: list[EngineWarning] = []
    fy0 = history.periods[-1]

    if a.eff("revenue_growth_fy1") > 0.25:
        warnings.append(EngineWarning(
            code="growth_fade_steep",
            message=(f"FY1 revenue growth {a.eff('revenue_growth_fy1'):.0%} "
                     f"(uncapped CAGR {a.eff('revenue_cagr_uncapped'):.0%}) — even "
                     "fading linearly, the cumulative 5y path is aggressive; the "
                     "curved fade is a documented v1.1 candidate.")))
    if a.fields["embedded_debt_rate"].derivation.startswith("IMPUTED"):
        warnings.append(EngineWarning(
            code="interest_imputed",
            message=("Interest expense imputed at the synthetic Kd: the filer "
                     "reports material debt but no separately tagged interest "
                     "expense. Omitting it would fabricate pretax income "
                     "(owner decision: impute unobservable expense, omit "
                     "unobservable income).")))

    wacc_build = build_wacc(history, market, a)
    wacc = wacc_build.wacc
    g = a.eff("terminal_growth")
    if g >= wacc:
        raise InvalidAssumptionError("terminal_growth",
                                     f"must be below WACC ({wacc:.2%})", g)

    projections = project(history, a)
    for p in projections:
        if p.balance["cash_and_equivalents"] < 0:
            warnings.append(EngineWarning(
                code="cash_plug_negative",
                message=(f"FY{p.fiscal_year}: the cash plug is negative "
                         f"(${p.balance['cash_and_equivalents'] / 1e9:.1f}B) — the "
                         "plan implies financing v1 does not model (no revolver); "
                         "review payout and capex assumptions.")))
            break

    stub = _stub(valuation_date, fy0.end)
    if stub > 1.0:
        warnings.append(EngineWarning(
            code="history_stale",
            message=(f"Latest fiscal year ended {fy0.end.isoformat()} — more than "
                     "a year before the valuation date. FY1 is partly or fully in "
                     "the past; refresh EDGAR data or expect stub-period artifacts.")))

    schedule = ufcf_schedule(projections, a, wacc, stub)
    if any(y.ufcf < 0 for y in schedule):
        warnings.append(EngineWarning(
            code="negative_ufcf",
            message="Negative unlevered FCF in explicit years: "
                    + ", ".join(f"FY{y.fiscal_year}" for y in schedule if y.ufcf < 0)))

    roic = _resolve_roic(a, g, wacc, warnings)
    gordon = terminal_gordon(projections, a, wacc, g, roic, stub)
    terminal = {"gordon": gordon}
    crosschecks: dict[str, float] = {}
    pv_explicit = sum(y.pv for y in schedule)
    ebitda_n = (projections[-1].income["operating_income"]
                + projections[-1].cashflow["d_and_a"])
    crosschecks["implied_exit_multiple"] = gordon.value_at_fyeN / ebitda_n

    bridges = {"gordon": build_bridge(history, a, "gordon",
                                      pv_explicit + gordon.pv)}
    multiple = a.eff("exit_multiple")
    if multiple is not None:
        exit_leg = terminal_exit(projections, multiple, wacc, stub)
        terminal["exit_multiple"] = exit_leg
        crosschecks["implied_terminal_g"] = (
            wacc - gordon.detail["fcf_terminal"] / exit_leg.value_at_fyeN)
        bridges["exit_multiple"] = build_bridge(history, a, "exit_multiple",
                                                pv_explicit + exit_leg.pv)

    checks = _checks(projections, history, wacc_build, a, terminal,
                     bridges["gordon"].enterprise_value, warnings)
    grids = sensitivity_grids(history, a, projections, stub, wacc)

    return ModelResult(
        ticker=history.company.ticker, valuation_date=valuation_date,
        history=history, market=market, assumptions=a, projections=projections,
        ufcf=schedule, wacc=wacc_build, terminal=terminal, crosschecks=crosschecks,
        bridges=bridges, sensitivity=grids, checks=checks, warnings=warnings)
