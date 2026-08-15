"""DCF: discounting, both terminal values, EV→equity bridge, sensitivity
grids, and the engine's single entry point `build_model` (specs/04-engine.md).

Pure and deterministic: ValuationDate is an input, never a clock read.
"""

from __future__ import annotations

from datetime import date

from ingest.models import CheckResult, FinancialHistory, ValidationReport
from market.models import MarketInputs

from .assumptions import (
    DISTRESSED_SPREAD,
    RATING_TABLE_AS_OF,
    TERMINAL_G_CEIL,
    apply_overrides,
    derive_assumptions,
    gross_debt,
)
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

TV_SHARE_INFO = 0.80          # P8 — published guidance: typical TV is 60–80%
                              # of EV; above 80% the valuation is a bet on
                              # long-run assumptions (audit task 4)
# Reinvestment-fade mismatch (audit task 5): final-year capex still >1.5× D&A
# while growth has faded to within 1pp of terminal g. Deliberately a WARNING,
# not an auto-fade — capex-equals-depreciation at steady state is itself
# contested (Matthews & Rosenbloom), and survey capex typically exceeds
# depreciation; the street_convention preset is the opinionated fade.
REINVEST_FADE_RATIO = 1.5
REINVEST_FADE_G_BAND = 0.01
EXCESS_RETURN_INFO = 0.10     # audit task 6: derived terminal ROIC more than
                              # 10pp above WACC → the model is asserting
                              # persistent excess returns (info flag)
LEASE_HEAVY = 0.25            # P6: operating leases vs gross debt
UNCLASSIFIED_WARN = 0.01      # same leg as H2's revenue-materiality (owner-approved)
WACC_STEP, G_STEP, MULT_STEP = 0.005, 0.0025, 1.0
GRID_OFFSETS = (-2, -1, 0, 1, 2)
# g axis only (owner decision 2026-08-14): same ±100bp span at 25bp steps —
# value is convex in g approaching WACC, so 50bp is coarsest exactly where the
# grid is most informative. Base case stays the center column.
G_OFFSETS = (-4, -3, -2, -1, 0, 1, 2, 3, 4)


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
    reinvestment. An explicit user- or preset-stated value that violates
    RR < 1 is rejected instead (their statement, their constraint)."""
    field = assumptions.fields["terminal_roic"]
    if field.stated is not None:
        if field.stated <= g:
            raise InvalidAssumptionError("terminal_roic",
                                         "must exceed terminal g (RR = g/ROIC < 1)",
                                         field.stated)
        return field.stated
    if field.value is None or field.value <= g:
        if warnings is not None:
            warnings.append(EngineWarning(
                code="roic_fallback",
                message=("Terminal ROIC set to WACC: terminal reinvestment is "
                         "value-neutral because returns could not be estimated "
                         "from history (invested capital ≤ 0 or ROIC ≤ g)."),
                detail={"derived_roic": field.value}))
        return wacc
    if assumptions.eff("terminal_roic_fade"):
        # midpoint of derived ROIC and WACC — excess-return decay toward the
        # cost of capital at stable growth (audit task 6; default OFF because
        # permanent moats are defensible too). Midpoint > g holds: both
        # inputs exceed g here (derived checked above, WACC > g by the block).
        return (field.value + wacc) / 2
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
                      base_wacc: float, gordon_available: bool = True,
                      exit_available: bool = True) -> dict[str, SensitivityGrid]:
    """Grids only for available legs — perturbing an unavailable leg (negative
    terminal anchor) would print 25 variations of a sign error."""
    g0 = assumptions.eff("terminal_growth")
    waccs = [base_wacc + o * WACC_STEP for o in GRID_OFFSETS]
    gs = [g0 + o * G_STEP for o in G_OFFSETS]
    grids = {}
    if gordon_available:
        grids["wacc_x_g"] = SensitivityGrid(
            row_label="WACC", col_label="terminal g", rows=waccs, cols=gs,
            cells=[[_value_per_share(history, assumptions, projections, stub, w,
                                     g=g, multiple=None)
                    for g in gs] for w in waccs])
    m0 = assumptions.eff("exit_multiple")
    if m0 is not None and exit_available:
        mults = [m0 + o * MULT_STEP for o in GRID_OFFSETS]
        grids["wacc_x_multiple"] = SensitivityGrid(
            row_label="WACC", col_label="exit EV/EBITDA", rows=waccs, cols=mults,
            cells=[[_value_per_share(history, assumptions, projections, stub, w,
                                     g=None, multiple=m)
                    for m in mults] for w in waccs])
    return grids


def _checks(projections: list[ProjectedPeriod], history: FinancialHistory,
            wacc_build: WaccBuild, assumptions: Assumptions,
            terminal: dict[str, TerminalLeg], ev_gordon: float | None,
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

    # P5 warns against the PUBLISHED constraint (g ≤ rf — audit task 2); the
    # engine's stricter house cap draws only an info flag (build_model).
    # Preset-stated g is treated exactly like a user override: presets never
    # suppress warnings, and the provenance is named.
    ceiling = a.eff("risk_free")
    tg = a.fields["terminal_growth"]
    p5_warn = tg.stated is not None and tg.stated > ceiling

    leases = fy0.value("operating_lease_liability", 0.0)
    p6_warn = leases > LEASE_HEAVY * expected_gross if expected_gross > 0 else leases > 0

    p7_codes = [w.code for w in warnings
                if w.code in ("cash_plug_negative", "negative_ufcf")]
    if wacc_build.beta_source == "fallback_1.0":
        p7_codes.append("beta_fallback")

    gordon_ok = "gordon" in terminal and ev_gordon is not None and ev_gordon > 0
    tv_share = terminal["gordon"].pv / ev_gordon if gordon_ok else None

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
                    detail=f"Terminal-g ({tg.provenance}) above the risk-free "
                           "rate — outside the published g ≤ rf constraint"
                    if p5_warn else
                    "Terminal g within the published g ≤ rf constraint"),
        CheckResult("P6", "warn", "warn" if p6_warn else "pass",
                    magnitude=leases,
                    detail=("Operating lease liability exceeds 25% of gross debt — "
                            "lease-exclusion convention materially binds (lease_heavy)"
                            if p6_warn else "Operating leases within bounds")),
        CheckResult("P7", "warn", "warn" if p7_codes else "pass",
                    detail=("Model-quality warnings: " + ", ".join(p7_codes))
                    if p7_codes else "No beta fallback, negative UFCF, or "
                                     "negative cash plug"),
        CheckResult("P8", "info",
                    "warn" if tv_share is None or tv_share > TV_SHARE_INFO
                    else "pass",
                    magnitude=tv_share,
                    detail=("Gordon leg unavailable (negative terminal anchor) — "
                            "terminal-value share not computable" if tv_share is None
                            else f"Terminal value = {tv_share:.0%} of enterprise value"
                            + ((" — above the 80% line, the valuation is a bet on "
                                "long-run assumptions rather than near-term "
                                "fundamentals. The remedy is a longer explicit "
                                "forecast horizon for this business; note a 5-year "
                                "horizon mechanically produces a higher TV share "
                                "than a 10-year one, so this firing often at the "
                                "current horizon is the signal working, not noise.")
                               if tv_share > TV_SHARE_INFO else ""))),
    ])


def build_model(history: FinancialHistory, market: MarketInputs,
                valuation_date: date, overrides: dict | None = None,
                assumptions: Assumptions | None = None,
                profile: str | None = "auto") -> ModelResult:
    """profile reaches derive_assumptions when assumptions aren't supplied:
    "auto" classifies, a tag reassigns (user), None skips the profile layer
    (mechanics tests pin defaults)."""
    a = assumptions or derive_assumptions(history, market, profile=profile)
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
    gap = a.eff("unclassified_costs_pct")
    if abs(gap) > UNCLASSIFIED_WARN:
        warnings.append(EngineWarning(
            code="unclassified_costs",
            message=(f"{gap:.1%} of revenue in operating costs is attributable "
                     "to no named line item (revenue − EBIT − Σ named cost "
                     "lines). Projected as an explicit unclassified-costs line "
                     "so the projected EBIT margin reproduces the filer's "
                     "historical margin identity — review the tag chains for "
                     "this filer."),
            detail={"unclassified_costs_pct": gap}))
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
    if wacc_build.spread >= DISTRESSED_SPREAD:
        warnings.append(EngineWarning(
            code="synthetic_rating_distressed",
            message=(f"Synthetic rating {wacc_build.rating} (coverage-implied "
                     f"spread {wacc_build.spread:.2%}): at this level the "
                     "synthetic-rating approach and the going-concern DCF "
                     "framing are both under strain — the reverse-DCF / "
                     "recovery view is more informative than the forward "
                     "model."),
            detail={"rating": wacc_build.rating, "spread": wacc_build.spread}))
    table_age_days = (valuation_date
                      - date(RATING_TABLE_AS_OF[0], RATING_TABLE_AS_OF[1], 1)).days
    if table_age_days > 548:                      # 18 months
        warnings.append(EngineWarning(
            code="rating_table_stale", severity="info",
            message=(f"The synthetic-rating spread table is dated "
                     f"{RATING_TABLE_AS_OF[0]}-{RATING_TABLE_AS_OF[1]:02d} — "
                     "more than 18 months before the valuation date. Credit "
                     "spreads move; refresh the table from the published "
                     "source."),
            detail={"as_of": f"{RATING_TABLE_AS_OF[0]}-{RATING_TABLE_AS_OF[1]:02d}"}))
    g = a.eff("terminal_growth")
    if g >= wacc:
        raise InvalidAssumptionError("terminal_growth",
                                     f"must be below WACC ({wacc:.2%})", g)
    rf = a.eff("risk_free")
    house_cap = min(TERMINAL_G_CEIL, rf)
    # Fires on ANY layer above the house cap — user, preset, or a profile
    # default (the compounder profile sets g at the ceiling; the disclosure
    # must not disappear because the source is a default).
    tg_field = a.fields["terminal_growth"]
    tg_disclosed = (tg_field.stated if tg_field.stated is not None
                    else (tg_field.value if tg_field.profile_tag else None))
    if tg_disclosed is not None and house_cap < tg_disclosed <= rf:
        warnings.append(EngineWarning(
            code="terminal_g_above_house_cap", severity="info",
            message=(f"Terminal growth {tg_disclosed:.2%} "
                     f"({tg_field.provenance}) is above the engine's "
                     f"conservative house cap min(2.5%, 10Y) = {house_cap:.2%} "
                     f"but within the published constraint g ≤ risk-free "
                     f"({rf:.2%})."),
            detail={"provenance": tg_field.provenance,
                    "house_cap": house_cap}))

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

    derived_roic = a.fields["terminal_roic"].value
    if (derived_roic is not None
            and derived_roic - wacc > EXCESS_RETURN_INFO):
        fade_on = bool(a.eff("terminal_roic_fade"))
        warnings.append(EngineWarning(
            code="terminal_excess_return_persistent", severity="info",
            message=(f"Derived terminal ROIC {derived_roic:.1%} exceeds WACC "
                     f"{wacc:.1%} by {derived_roic - wacc:.1%}. "
                     + ("terminal_roic_fade is ON: ROIC_t is set to the "
                        "midpoint of the two, so half that excess persists."
                        if fade_on else
                        "The model is assuming those excess returns persist "
                        "in perpetuity (terminal_roic_fade is off).")),
            detail={"derived_roic": derived_roic, "wacc": wacc,
                    "spread": derived_roic - wacc}))

    fy_last, fy_prev = projections[-1], projections[-2]
    capex_n = fy_last.cashflow["capex"]
    da_n = fy_last.cashflow["d_and_a"]
    g_n = fy_last.income["revenue"] / fy_prev.income["revenue"] - 1
    if (da_n > 0 and capex_n / da_n > REINVEST_FADE_RATIO
            and abs(g_n - g) <= REINVEST_FADE_G_BAND):
        warnings.append(EngineWarning(
            code="reinvestment_fade_mismatch",
            message=(f"FY{fy_last.fiscal_year}: capex is {capex_n / da_n:.1f}× "
                     f"D&A while revenue growth has faded to {g_n:.1%} "
                     f"(terminal g {g:.1%}) — elevated growth-era reinvestment "
                     "is carried into a period modeled as near-mature. Capex is "
                     "deliberately NOT auto-faded (capex-equals-depreciation at "
                     "steady state is itself contested); the street_convention "
                     "preset expresses a fade toward D&A parity if that is "
                     "your view."),
            detail={"capex_over_da": capex_n / da_n, "final_year_growth": g_n}))

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

    # Negative terminal anchors (owner-approved): a perpetuity or a multiple
    # on a negative FY5 base is not a valuation — it is a sign error dressed
    # as a number (KHC post-impairment, BA loss window). Each leg reports an
    # honest unavailable state with the anchor named; the reverse DCF stays
    # available (the implied recovery margin is informative even here).
    fy5 = projections[-1]
    ebitda_n = fy5.income["operating_income"] + fy5.cashflow["d_and_a"]
    nopat_anchor = (fy5.income["operating_income"] * (1 + g)
                    * (1 - a.eff("marginal_tax")))
    terminal: dict[str, TerminalLeg] = {}
    crosschecks: dict[str, float] = {}
    bridges: dict[str, Bridge] = {}
    pv_explicit = sum(y.pv for y in schedule)

    if nopat_anchor > 0:
        roic = _resolve_roic(a, g, wacc, warnings)
        gordon = terminal_gordon(projections, a, wacc, g, roic, stub)
        terminal["gordon"] = gordon
        crosschecks["implied_exit_multiple"] = gordon.value_at_fyeN / ebitda_n
        bridges["gordon"] = build_bridge(history, a, "gordon",
                                         pv_explicit + gordon.pv)
    else:
        warnings.append(EngineWarning(
            code="terminal_anchor_negative",
            message=(f"Gordon terminal value unavailable: projected FY5 EBIT is "
                     f"{fy5.income['operating_income'] / 1e9:,.1f}B — the "
                     "terminal NOPAT anchor is negative, and a perpetuity on a "
                     "negative base is a sign error, not a valuation. A value "
                     "here requires an explicit recovery view (see the reverse "
                     "DCF, which remains available)."),
            detail={"fy5_ebit": fy5.income["operating_income"], "leg": "gordon"}))

    multiple = a.eff("exit_multiple")
    if multiple is not None and ebitda_n > 0:
        exit_leg = terminal_exit(projections, multiple, wacc, stub)
        terminal["exit_multiple"] = exit_leg
        if "gordon" in terminal:
            crosschecks["implied_terminal_g"] = (
                wacc - terminal["gordon"].detail["fcf_terminal"]
                / exit_leg.value_at_fyeN)
        bridges["exit_multiple"] = build_bridge(history, a, "exit_multiple",
                                                pv_explicit + exit_leg.pv)
    elif multiple is not None:
        warnings.append(EngineWarning(
            code="terminal_anchor_negative",
            message=(f"Exit-multiple terminal value unavailable: projected FY5 "
                     f"EBITDA is {ebitda_n / 1e9:,.1f}B — a multiple of a "
                     "negative EBITDA is not a value."),
            detail={"fy5_ebitda": ebitda_n, "leg": "exit_multiple"}))

    checks = _checks(projections, history, wacc_build, a, terminal,
                     bridges["gordon"].enterprise_value
                     if "gordon" in bridges else None, warnings)
    grids = sensitivity_grids(history, a, projections, stub, wacc,
                              gordon_available="gordon" in terminal,
                              exit_available="exit_multiple" in terminal)

    return ModelResult(
        ticker=history.company.ticker, valuation_date=valuation_date,
        history=history, market=market, assumptions=a, projections=projections,
        ufcf=schedule, wacc=wacc_build, terminal=terminal, crosschecks=crosschecks,
        bridges=bridges, sensitivity=grids, checks=checks, warnings=warnings)
