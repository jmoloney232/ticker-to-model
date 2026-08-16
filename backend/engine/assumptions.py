"""derive_assumptions(history, market) — every default from the owner-approved
derivation table (specs/04-engine.md). Pure.

Notation: FY0 = latest historical year; "3y mean" = arithmetic mean of the
per-year ratio over the last min(3, n) fiscal years — per-year then averaged,
so a mix-shift year stays visible. 53-week years enter unadjusted (ingest H7
annotates them).
"""

from __future__ import annotations

from ingest.models import FinancialHistory, FiscalPeriod
from market.models import MarketInputs

from .errors import InvalidAssumptionError
from .models import Assumption, Assumptions

# EPV margin normalization by profile — rule NAMES, parity-tested against
# methodology.yaml epv_margin_normalization.rules (owner-approved 2026-08-15)
EPV_MARGIN_RULES = {
    "mature": "trailing_3y_mean",
    "compounder": "trailing_3y_mean",
    "cyclical": "full_window_mean",
    "declining": "window_median",
    "collision": "declining_wins",
}
GROWTH_CAP = 0.30            # owner decision: the default must be defensible;
GROWTH_WARN = 0.25           # the uncapped CAGR is displayed alongside
MARGINAL_TAX = 0.25          # 21% federal + state blend (methodology: tax_rate)
EFF_TAX_CLAMP = (0.10, 0.35)
ERP = 0.05
TERMINAL_G_FLOOR, TERMINAL_G_CEIL = 0.015, 0.025
CASH_FLOOR_PCT = 0.02

# Synthetic-rating spread table — Damodaran, "Ratings, Interest Coverage
# Ratios and Default Spread", LARGE non-financial firms table, data as of
# 2026-01 (the small-cap table has different breakpoints; this engine is
# scoped to large caps). Constants here because the engine does no I/O; a
# parity test asserts these match methodology.yaml's spread_table exactly.
# The final bracket's floor is an effective -inf: negative-coverage filers
# land in D, not in a truncated "everything else" bucket — truncating the
# distressed range understates Kd exactly where it matters most.
RATING_TABLE_AS_OF = (2026, 1)
SPREAD_TABLE: list[tuple[float, str, float]] = [
    (8.5, "Aaa/AAA", 0.0040),
    (6.5, "Aa2/AA", 0.0055),
    (5.5, "A1/A+", 0.0070),
    (4.25, "A2/A", 0.0078),
    (3.0, "A3/A-", 0.0089),
    (2.5, "Baa2/BBB", 0.0111),
    (2.25, "Ba1/BB+", 0.0138),
    (2.0, "Ba2/BB", 0.0184),
    (1.75, "B1/B+", 0.0275),
    (1.5, "B2/B", 0.0321),
    (1.25, "B3/B-", 0.0509),
    (0.8, "Caa/CCC", 0.0885),
    (0.65, "Ca2/CC", 0.1261),
    (0.2, "C2/C", 0.1600),
    (-100000.0, "D2/D", 0.1900),
]
# The Caa/CCC spread: at or above this bracket the synthetic-rating method and
# the going-concern DCF framing are both under strain (warning in build_model).
DISTRESSED_SPREAD = next(s for _, r, s in SPREAD_TABLE if r == "Caa/CCC")


def rating_for_coverage(coverage: float | None) -> tuple[str, float]:
    """None coverage = no traceable interest → top bracket, disclosed."""
    if coverage is None:
        return SPREAD_TABLE[0][1], SPREAD_TABLE[0][2]
    for floor, rating, spread in SPREAD_TABLE:
        if coverage > floor:
            return rating, spread
    return SPREAD_TABLE[-1][1], SPREAD_TABLE[-1][2]


def gross_debt(p: FiscalPeriod) -> float:
    """ST + LT debt; finance leases are already inside via the ingest chains."""
    return p.value("short_term_debt", 0.0) + p.value("long_term_debt", 0.0)


def cost_basis(p: FiscalPeriod, cost_structure: str) -> float:
    """DIO/DPO denominator. by_nature has no COGS: total operating costs
    (revenue − EBIT) substitute — safe because the same denominator drives the
    historical ratio and the projection, so distortion cancels in-model. A
    projection ratio, never a comparable DIO/DPO figure (owner decision)."""
    if cost_structure == "by_function":
        return p.value("cost_of_revenue", 0.0)
    return p.value("revenue", 0.0) - p.value("operating_income", 0.0)


def _window(history: FinancialHistory) -> list[FiscalPeriod]:
    return history.periods[-min(3, len(history.periods)):]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _median(values: list[float]) -> float:
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _pct_of_revenue(window: list[FiscalPeriod], item: str) -> float:
    return _mean([p.value(item, 0.0) / p.value("revenue") for p in window])


def _prior_pairs(history: FinancialHistory) -> list[tuple[FiscalPeriod, FiscalPeriod]]:
    """(prior, current) pairs, most recent min(3, available) — for ratios that
    need a beginning balance."""
    pairs = list(zip(history.periods, history.periods[1:], strict=False))
    return pairs[-min(3, len(pairs)):]


def derive_assumptions(history: FinancialHistory, market: MarketInputs,
                       profile: str | None = "auto") -> Assumptions:
    """Derived defaults, profile-aware (owner-approved 2026-08-15).
    profile: "auto" → classify from the filer's own measurements;
    a tag like "mature" or "compounder+cyclical" → user reassignment
    (provenance discloses it); None → base defaults, no profile layer
    (internal/tests — mechanics under fixed defaults)."""
    periods = history.periods
    fy0 = periods[-1]
    w = _window(history)
    cs = history.cost_structure
    rf = market.risk_free.value
    a: dict[str, Assumption] = {}

    def add(name, value, derivation, unit="ratio"):
        a[name] = Assumption(name=name, value=value, derivation=derivation, unit=unit)

    # ── revenue growth (capped CAGR + linear fade; owner decision) ─────────
    k = min(3, len(periods) - 1)
    cagr = (fy0.value("revenue") / periods[-1 - k].value("revenue")) ** (1 / k) - 1
    add("revenue_growth_fy1", min(cagr, GROWTH_CAP),
        f"{k}y revenue CAGR = {cagr:.1%}, capped at {GROWTH_CAP:.0%} "
        f"(uncapped CAGR shown so nothing is hidden; override upward allowed)",
        unit="rate")
    add("revenue_cagr_uncapped", cagr,
        f"Uncapped {k}y CAGR — display only; the capped default drives FY1", unit="rate")

    # ── cost lines, D&A-inclusive as filed (owner decision: memo D&A) ──────
    if cs == "by_function":
        add("cogs_pct", _pct_of_revenue(w, "cost_of_revenue"),
            "3y mean cost of revenue / revenue (D&A-inclusive as filed)")
    for item, name in (("research_and_development", "rnd_pct"),
                       ("selling_general_admin", "sga_pct"),
                       ("other_operating", "other_opex_pct")):
        add(name, _pct_of_revenue(w, item),
            f"3y mean {item} / revenue (D&A-inclusive as filed)")

    # Margin-identity closure (owner-approved fix, diagnostic-pass catch):
    # costs living in NO named line item — revenue − EBIT − Σ(named cost
    # lines) — are projected as an explicit ratio, so the projected EBIT
    # margin reproduces the filer's own historical margin structure by
    # identity. Without this line, an MCD-class filer (a real-but-tiny
    # other_operating tag blocking the residual deriver) projected an 89%
    # EBIT margin against a real 46%.
    add("unclassified_costs_pct", _mean([_identity_gap(p) for p in w]),
        "3y mean (revenue − EBIT − Σ named cost lines) / revenue — the "
        "margin-identity closure line; costs attributable to no named item, "
        "projected explicitly (never silently absorbed into the margin)")

    # ── D&A memo rate (PP&E roll; % of revenue fallback) ───────────────────
    da_ratios = [(cur.value("d_and_a", 0.0), prev.value("ppe_net", 0.0))
                 for prev, cur in _prior_pairs(history)]
    usable = [d / ppe for d, ppe in da_ratios if ppe > 0]
    if usable:
        add("da_pct_beginning_ppe", _mean(usable),
            "3y mean D&A / beginning net PP&E (memo line: CF add-back, PP&E roll, "
            "EBITDA — never subtracted from ratio-projected cost lines)")
    else:
        add("da_pct_revenue", _pct_of_revenue(w, "d_and_a"),
            "PP&E unmapped → 3y mean D&A / revenue (disclosed fallback)")

    add("capex_pct", _pct_of_revenue(w, "capex"), "3y mean capex / revenue")
    add("sbc_pct", _pct_of_revenue(w, "stock_compensation"),
        "3y mean stock compensation / revenue (0 when unmapped — see warnings)")

    # ── working capital ────────────────────────────────────────────────────
    add("dso", _mean([365 * p.value("accounts_receivable", 0.0) / p.value("revenue")
                      for p in w]), "3y mean of 365 · AR / revenue", unit="days")
    basis_note = ("COGS" if cs == "by_function"
                  else "total operating costs (revenue − EBIT; by-nature filer — "
                       "projection ratio, not a comparable DIO/DPO)")
    dio = [365 * p.value("inventory", 0.0) / b for p in w if (b := cost_basis(p, cs)) > 0]
    dpo = [365 * p.value("accounts_payable", 0.0) / b
           for p in w if (b := cost_basis(p, cs)) > 0]
    add("dio", _mean(dio), f"3y mean of 365 · inventory / {basis_note}", unit="days")
    add("dpo", _mean(dpo), f"3y mean of 365 · AP / {basis_note}", unit="days")
    for item, name in (("other_current_assets", "oca_pct"),
                       ("accrued_liabilities", "accrued_pct"),
                       ("other_current_liabilities", "ocl_pct"),
                       ("deferred_revenue_current", "defrev_pct")):
        add(name, _pct_of_revenue(w, item), f"3y mean {item} / revenue")

    # ── taxes ──────────────────────────────────────────────────────────────
    eff_years = [p.value("income_tax", 0.0) / p.value("pretax_income")
                 for p in w if p.value("pretax_income", 0.0) > 0]
    eff = (min(max(_mean(eff_years), EFF_TAX_CLAMP[0]), EFF_TAX_CLAMP[1])
           if eff_years else MARGINAL_TAX)
    add("effective_tax_fy1", eff,
        "3y mean income tax / pretax (loss years excluded), clamped 10–35%; "
        "fades linearly to marginal by FY5", unit="rate")
    add("marginal_tax", MARGINAL_TAX,
        "21% federal + state blend; terminal NOPAT and after-tax Kd both use this",
        unit="rate")

    # ── payout ─────────────────────────────────────────────────────────────
    payout_years = [p.value("dividends_paid", 0.0) / p.value("net_income")
                    for p in w if p.value("net_income", 0.0) > 0]
    add("payout_ratio", min(max(_mean(payout_years), 0.0), 1.0) if payout_years else 0.0,
        "3y mean dividends / net income over positive-NI years, clamped [0, 1]")

    # ── interest (owner decision: omit unobservable income, impute expense) ─
    coverage = _coverage(w)
    rating, spread = rating_for_coverage(coverage)
    emb = [(cur.value("interest_expense", 0.0), gross_debt(prev))
           for prev, cur in _prior_pairs(history)]
    emb_usable = [ie / d for ie, d in emb if d > 0]
    ie_fact = fy0.income.get("interest_expense")
    interest_unobservable = (ie_fact is None or ie_fact.source == "zero_logged")
    if interest_unobservable and gross_debt(fy0) > 0:
        add("embedded_debt_rate", rf + spread,
            f"IMPUTED at synthetic Kd (rf + {rating} spread): interest expense is "
            "unmapped while debt is material — $0 interest on real debt would "
            "fabricate pretax income (owner decision; see warnings)", unit="rate")
    else:
        add("embedded_debt_rate", _mean(emb_usable) if emb_usable else 0.0,
            "3y mean interest expense / beginning gross debt (legacy-coupon rate; "
            "the WACC uses the marginal synthetic rate — different questions)",
            unit="rate")

    yield_years = [(cur.value("interest_income", 0.0),
                    prev.value("cash_and_equivalents", 0.0)
                    + prev.value("short_term_investments", 0.0))
                   for prev, cur in _prior_pairs(history)]
    yield_usable = [ii / c for ii, c in yield_years if c > 0]
    add("interest_income_yield", min(max(_mean(yield_usable), 0.0), rf),
        "3y mean interest income / beginning (cash + ST investments), clamped "
        "[0, rf]; 0 when unmapped — unobservable income is omitted, not invented",
        unit="rate")

    # ── cost of capital ────────────────────────────────────────────────────
    if market.beta is not None:
        add("beta", market.beta.adjusted,
            f"2y weekly OLS vs {market.beta.benchmark}, Blume-adjusted "
            f"(raw {market.beta.raw:.3f}, n={market.beta.n_obs}, "
            f"R²={market.beta.r_squared:.2f})", unit="x")
        add("beta_raw", market.beta.raw, "Raw OLS beta (display + toggle)", unit="x")
    else:
        add("beta", 1.0, "FALLBACK: market-average beta — price history too short "
                         "or benchmark unavailable (see warnings); override "
                         "recommended", unit="x")
    add("erp", ERP, "Equity risk premium — 4.5–6% all defensible; 5% mid-range",
        unit="rate")
    add("risk_free", rf,
        f"Spot 10Y Treasury (DGS10, {market.risk_free.staleness}); "
        "normalized-rate override supported", unit="rate")
    add("coverage_ratio", coverage,
        "Σ3y EBIT / Σ3y interest expense (ratio of sums — a near-zero interest "
        "year would explode a per-year ratio); None = no traceable interest → "
        "top rating bracket, disclosed", unit="x")

    # ── terminal ───────────────────────────────────────────────────────────
    add("terminal_growth", max(TERMINAL_G_FLOOR, min(TERMINAL_G_CEIL, rf)),
        "max(1.5%, min(2.5%, 10Y)) — the engine's conservative HOUSE CAP, a "
        "deliberate deviation from the published rule (Damodaran: g ≤ rf); "
        "floored so distorted-rate regimes don't mechanically crush the "
        "valuation. The rf ceiling is displayed alongside so the cap hides "
        "nothing", unit="rate")
    add("terminal_growth_rf_ceiling", rf,
        "Current 10Y — the published ceiling for terminal g (g ≤ rf: the "
        "risk-free rate embeds long-run growth and inflation assumptions the "
        "cash flows should share). Display only, beside the house-capped "
        "default", unit="rate")
    roic_years = []
    for prev, cur in _prior_pairs(history):
        ic = (gross_debt(prev) + prev.value("stockholders_equity", 0.0)
              + prev.value("noncontrolling_interest", 0.0)
              + prev.value("preferred_equity", 0.0)
              + prev.value("temporary_equity", 0.0)
              - prev.value("cash_and_equivalents", 0.0)
              - prev.value("short_term_investments", 0.0))
        if ic > 0:
            roic_years.append(cur.value("operating_income") * (1 - MARGINAL_TAX) / ic)
    add("terminal_roic", _mean(roic_years) if roic_years else None,
        "3y mean marginal-taxed NOPAT / beginning invested capital (gross debt + "
        "equity incl. NCI/preferred/temporary − cash − ST investments); None → "
        "falls back to WACC (value-neutral reinvestment) with a warning", unit="rate")

    # ── earnings power (EPV) ───────────────────────────────────────────────
    # Normalized EBIT margin capitalized at WACC with maintenance capex = D&A
    # and working capital held flat — under that simplification D&A cancels
    # and EPV reduces to a perpetuity on normalized NOPAT. Profiles restate
    # the normalization window (owner-approved 2026-08-15): cyclical → full
    # observed window; declining → latest year (declining wins the collision);
    # compounder/mature keep the house trailing-3y mean.
    add("epv_margin", _mean([p.value("operating_income") / p.value("revenue")
                             for p in w]),
        "EPV normalized EBIT margin — trailing 3y mean operating margin "
        "(house rule; a rising-margin filer is valued a touch conservatively, "
        "the right direction for a no-growth number)")

    # ── exit multiple ──────────────────────────────────────────────────────
    shares = _share_proxy(history)
    add("share_count", shares,
        "Current cover-page basic shares × latest diluted/basic WA ratio "
        "(TSM approximation; provenance travels with ingest warnings)", unit="shares")
    ebitda0 = fy0.value("operating_income") + fy0.value("d_and_a", 0.0)
    ev0 = (market.price.value * shares + gross_debt(fy0)
           - fy0.value("cash_and_equivalents", 0.0)
           - fy0.value("short_term_investments", 0.0))
    add("exit_multiple", ev0 / ebitda0 if ebitda0 > 0 else None,
        "Current EV / FY0 EBITDA (combined-unsplit investments excluded per the "
        "ingest decision); assumes today's multiple persists — see the implied-g "
        "cross-check; None when EBITDA ≤ 0 (exit leg unavailable)", unit="x")

    add("cash_floor_pct", CASH_FLOOR_PCT,
        "Operating-cash floor: cash below 2% of revenue is operating, not excess")

    # ── horizon ────────────────────────────────────────────────────────────
    add("forecast_years", 5,
        "Explicit forecast horizon — 5 (default), 7, or 10 years. Koller et "
        "al. recommend extending until steady state, but a mechanically "
        "extrapolated linear fade over 10–15 years can be worse than a short "
        "horizon, so 5 stays the default and longer is a choice, never a "
        "recommendation. All fades (growth, tax) reach their terminal levels "
        "in the final year. Structural in the exported workbook: the sheet is "
        "laid out at this horizon — change it in the app and re-download, "
        "not in the workbook cell", unit="years")

    # ── toggles ────────────────────────────────────────────────────────────
    add("midyear", True, "Mid-year discounting (Gordon TV at N−0.5; exit TV at N "
                         "— deliberate asymmetry)", unit="flag")
    add("sbc_addback", False, "SBC stays a real expense (owner-confirmed); toggle "
                              "re-adds it as non-cash", unit="flag")
    add("kd_synthetic", True,
        "Synthetic-rating Kd default (Damodaran large-firm table, as of "
        f"{RATING_TABLE_AS_OF[0]}-{RATING_TABLE_AS_OF[1]:02d}); toggle = "
        "embedded legacy rate. Disclosed: synthetic rating is a fallback "
        "method intended for unrated issuers — most companies in this "
        "universe carry an actual agency rating the engine does not yet "
        "ingest (see known-limitations)", unit="flag")
    add("beta_adjusted", True, "Blume adjustment on (⅔β + ⅓)", unit="flag")
    add("fade_curved", False,
        "Growth fade shape. OFF: linear from FY1 growth to terminal g. ON: "
        "half-cosine — front-loaded persistence (a durable franchise keeps "
        "its growth premium in the near years) with a smooth landing into "
        "the perpetuity, no free parameter. Set ON by the compounder "
        "profile; editable", unit="flag")
    add("capex_fade", False,
        "OFF: capex held at the trailing rate all forecast years. ON: capex "
        "% of revenue fades linearly to the maintenance level "
        "(capex_terminal_pct) by the final year — the documented remedy for "
        "the reinvestment_fade_mismatch warning. Set ON by the "
        "reinvestment-heavy profile; editable", unit="flag")
    add("capex_terminal_pct",
        _pct_of_revenue(w, "d_and_a") * (1 + a["terminal_growth"].value),
        "Maintenance capex: trailing 3y D&A/revenue × (1 + terminal g) — "
        "replacement burden plus the growth increment on the asset base. "
        "Used only when capex_fade is ON; perpetual reinvestment after the "
        "horizon is RR = g/ROIC's job, not this line's")
    add("terminal_roic_fade", False,
        "OFF (default): terminal ROIC holds the 3y historical level in "
        "perpetuity — permanent excess returns, defensible for durable moats "
        "but an assertion. ON: ROIC_t = midpoint(derived ROIC, WACC) — excess "
        "returns decay toward the cost of capital at stable growth "
        "(Damodaran's stated guidance for stable-growth firms). An explicit "
        "user/preset ROIC always wins over the toggle", unit="flag")

    out = Assumptions(fields=a, cost_structure=cs)
    if profile is not None:
        _classify_and_apply(out, history, market, profile)
    return out


# ── company profiles (owner-approved 2026-08-15) ─────────────────────────────
# Classification measurements use THIS module's definitions (ROIC, margins,
# windows) so the profile is judged by the same yardsticks as the defaults it
# adjusts. WACC is built BEFORE application, so a profile structurally cannot
# move it — asserted in tests.


def _profile_measures(history: FinancialHistory, wacc: float):
    from .profile import ProfileMeasures
    ps = history.periods
    rev = [p.value("revenue") for p in ps]
    n_years = len(ps) - 1
    margins = [p.value("operating_income") / p.value("revenue") for p in ps]

    roics = []
    for prev, cur in zip(ps, ps[1:], strict=False):
        ic = (gross_debt(prev) + prev.value("stockholders_equity", 0.0)
              + prev.value("noncontrolling_interest", 0.0)
              + prev.value("preferred_equity", 0.0)
              + prev.value("temporary_equity", 0.0)
              - prev.value("cash_and_equivalents", 0.0)
              - prev.value("short_term_investments", 0.0))
        if ic > 0:
            roics.append(cur.value("operating_income") * (1 - MARGINAL_TAX) / ic)

    capex_da = []
    for p in ps[-3:]:
        da = p.value("d_and_a", 0.0)
        if da > 0:
            capex_da.append(abs(p.value("capex", 0.0)) / da)

    return ProfileMeasures(
        cagr=(rev[-1] / rev[0]) ** (1 / n_years) - 1,
        g_latest=rev[-1] / rev[-2] - 1,
        roic_median=_median(roics) if roics else None,
        roic_years_above_wacc=sum(1 for r in roics if r > wacc),
        roic_years=len(roics),
        wacc=wacc,
        margin_range=max(margins) - min(margins),
        rev_down_years=sum(1 for x, y in zip(rev, rev[1:], strict=False) if y < x),
        capex_da=_mean(capex_da) if capex_da else None,
        window=len(ps),
    )


def _classify_and_apply(a: Assumptions, history: FinancialHistory,
                        market: MarketInputs, requested: str) -> None:
    from .profile import Profile, classify, parse_profile
    from .wacc import build_wacc  # lazy: wacc.py imports this module
    wacc = build_wacc(history, market, a).wacc
    measures = _profile_measures(history, wacc)
    auto = classify(measures)
    if requested == "auto":
        prof = auto
    else:
        primary, mods = parse_profile(requested)
        prof = Profile(primary=primary, modifiers=mods, measures=measures,
                       reassigned=True, notes=auto.notes)
    _apply_profile(a, prof, history, market)
    a.profile = prof


def _apply_profile(a: Assumptions, prof, history: FinancialHistory,
                   market: MarketInputs) -> None:
    """Rewrite the DEFAULTS a profile owns — horizon, fade shape, terminal
    growth, margin normalization, capex normalization. Nothing else: WACC
    components, the bridge, and validation thresholds are out of bounds.
    Presets and user overrides still layer on top unchanged."""
    tag = prof.tag

    def set_default(name: str, value, derivation: str) -> None:
        f = a.fields[name]
        f.value = value
        f.derivation = derivation
        f.profile_tag = tag

    if prof.primary == "compounder":
        rf = market.risk_free.value
        m = prof.measures
        set_default("forecast_years", 10,
                    "Profile: growth ≥ 2× trend with durable excess returns "
                    f"(CAGR {m.cagr:.1%}, ROIC−WACC "
                    f"{(m.roic_median or 0) - m.wacc:+.1%}) — a 5-year window "
                    "forces convergence a compounder hasn't shown; horizon "
                    "extended to 10")
        set_default("fade_curved", True,
                    "Profile: half-cosine fade — front-loaded growth "
                    "persistence, smooth landing into the perpetuity")
        set_default("terminal_growth", rf,
                    f"Profile: default at the published g ≤ 10Y ceiling "
                    f"({rf:.2%}) — durable compounding priced at the "
                    "risk-free rate's embedded nominal growth; the house-cap "
                    "note stays visible")
    elif prof.primary == "declining":
        m = prof.measures
        old = a.fields["terminal_growth"].value
        anchored = max(-0.02, min(old, m.cagr))
        set_default("terminal_growth", anchored,
                    f"Profile: trailing trajectory {m.cagr:+.1%} (latest year "
                    f"{m.g_latest:+.1%}) is below inflation — terminal g "
                    "anchored to the company's own path, floored at −2%, "
                    "never above the mature default. A decline is not "
                    "assumed to reverse")

    if "cyclical" in prof.modifiers:
        w_full = history.periods
        cs = a.cost_structure
        if cs == "by_function" and a.has("cogs_pct"):
            set_default("cogs_pct", _pct_of_revenue(w_full, "cost_of_revenue"),
                        f"Profile: {len(w_full)}y full-cycle mean cost of "
                        "revenue / revenue (cyclical — trailing 3y would "
                        "anchor on one phase of the cycle)")
        for item, name in (("research_and_development", "rnd_pct"),
                           ("selling_general_admin", "sga_pct"),
                           ("other_operating", "other_opex_pct")):
            set_default(name, _pct_of_revenue(w_full, item),
                        f"Profile: {len(w_full)}y full-cycle mean {item} / "
                        "revenue (cyclical)")
        set_default("unclassified_costs_pct",
                    _mean([_identity_gap(p) for p in w_full]),
                    f"Profile: {len(w_full)}y full-cycle margin-identity "
                    "closure (cyclical)")

    # EPV margin normalization (owner-approved 2026-08-15). Precedence when
    # declining AND cyclical collide (KHC): declining wins — secular decline
    # means old cycle peaks aren't coming back, and a full-window average
    # would capitalize a base the company demonstrably no longer has. The
    # DCF's cost lines above still use the full-cycle window (different
    # question: projecting a cost structure forward vs. capitalizing today's
    # demonstrated earnings power).
    if prof.primary == "declining":
        w_full = history.periods
        set_default("epv_margin",
                    _median([p.value("operating_income") / p.value("revenue")
                             for p in w_full]),
                    f"Profile: {len(w_full)}y window MEDIAN operating margin "
                    "— robust to a single distorted year (impairment, "
                    "charge) without classifying anything as non-recurring; "
                    "a mean would average the distortion in, the latest year "
                    "might BE the distortion (KHC). The decline itself "
                    "belongs in the DCF − EPV growth gap (declining wins "
                    "over cyclical here; owner-approved 2026-08-16)")
    elif "cyclical" in prof.modifiers:
        w_full = history.periods
        set_default("epv_margin",
                    _mean([p.value("operating_income") / p.value("revenue")
                           for p in w_full]),
                    f"Profile: {len(w_full)}y full-cycle mean operating "
                    "margin — capitalizing one phase of the cycle is the "
                    "classic cyclicals error")

    if "reinvestment_heavy" in prof.modifiers:
        m = prof.measures
        set_default("capex_fade", True,
                    f"Profile: capex runs {m.capex_da:.2f}× D&A — held flat "
                    "forever that contradicts terminal growth (the "
                    "reinvestment_fade_mismatch warning's exact finding); "
                    "capex fades to maintenance by the final year")


# Computed context, not inputs: shown beside their editable partners so no
# cap or convention hides anything; never overridable, never named-range inputs.
DISPLAY_ONLY = frozenset({"revenue_cagr_uncapped", "terminal_growth_rf_ceiling"})


def _identity_gap(p: FiscalPeriod) -> float:
    named = (p.value("cost_of_revenue", 0.0)
             + p.value("research_and_development", 0.0)
             + p.value("selling_general_admin", 0.0)
             + p.value("other_operating", 0.0))
    return (p.value("revenue") - p.value("operating_income") - named) / p.value("revenue")


def _coverage(window: list[FiscalPeriod]) -> float | None:
    total_interest = sum(p.value("interest_expense", 0.0) for p in window)
    if total_interest <= 0:
        return None
    return sum(p.value("operating_income") for p in window) / total_interest


def _share_proxy(history: FinancialHistory) -> float:
    fy0 = history.periods[-1]
    basic = fy0.value("shares_basic_wa")
    diluted = fy0.value("shares_diluted_wa", basic)
    ratio = diluted / basic if basic else 1.0
    return history.shares_current.value * ratio


# Override domains: hard constraints checked at apply time; cross-field
# constraints (g < WACC, RR < 1) are P4's job at build time.
_DOMAINS: dict[str, tuple[float, float, str]] = {
    "revenue_growth_fy1": (-0.50, 1.00, "FY1 growth within [-50%, +100%]"),
    # widened from [0%, 6%] for the preset feature (2026-08-14): market-implied
    # terminal growth legitimately reaches 6-10% (MSFT 6.2%) and below zero
    # (VZ -0.7% — a decliner-in-perpetuity view). The HARD constraint is and
    # remains g < WACC (P4 blocks); the domain only catches nonsense inputs,
    # and P5 warns on any stated g above the default ceiling.
    "terminal_growth": (-0.02, 0.10, "terminal g within [-2%, 10%]"),
    "effective_tax_fy1": (0.0, 0.60, "tax rate within [0%, 60%]"),
    "marginal_tax": (0.0, 0.60, "tax rate within [0%, 60%]"),
    "payout_ratio": (0.0, 1.0, "payout within [0, 1]"),
    "beta": (0.0, 4.0, "beta within (0, 4]"),
    "erp": (0.01, 0.12, "ERP within [1%, 12%]"),
    "risk_free": (0.0, 0.15, "risk-free within [0%, 15%]"),
    "exit_multiple": (0.0, 100.0, "multiple within (0, 100]"),
    "share_count": (1.0, 1e12, "share count positive"),
    "terminal_roic": (0.0, 2.0, "ROIC within (0, 200%]"),
    "cash_floor_pct": (0.0, 0.25, "cash floor within [0%, 25%]"),
    # a stated negative margin is a coherent (if bleak) view — EPV then goes
    # honestly unavailable rather than blocking the input
    "epv_margin": (-0.50, 0.60, "EPV margin within [-50%, 60%]"),
}

# Discrete domains: structural inputs where only named values are coherent
# (a 6-year fade is expressible but the workbook layout, docs, and tests
# commit to the supported set).
_DISCRETE_DOMAINS: dict[str, tuple[frozenset, str]] = {
    "forecast_years": (frozenset({5, 7, 10}),
                       "forecast horizon must be 5, 7, or 10 years"),
}


def check_domain(name: str, value) -> None:
    """Shared by user overrides and preset values — one validation, no
    second, laxer path."""
    if isinstance(value, bool):
        return
    if name in _DISCRETE_DOMAINS:
        allowed, label = _DISCRETE_DOMAINS[name]
        if float(value) not in allowed:
            raise InvalidAssumptionError(name, label, value)
        return
    if name in _DOMAINS:
        lo, hi, label = _DOMAINS[name]
        if not (lo <= float(value) <= hi):
            raise InvalidAssumptionError(name, label, value)


def apply_overrides(assumptions: Assumptions, overrides: dict[str, float | bool]
                    ) -> Assumptions:
    for name, value in overrides.items():
        if name not in assumptions.fields:
            raise InvalidAssumptionError(name, "not an assumption field", value)
        check_domain(name, value)
        assumptions.fields[name].override = value
    return assumptions
