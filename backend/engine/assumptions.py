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

GROWTH_CAP = 0.30            # owner decision: the default must be defensible;
GROWTH_WARN = 0.25           # the uncapped CAGR is displayed alongside
MARGINAL_TAX = 0.25          # 21% federal + state blend (methodology: tax_rate)
EFF_TAX_CLAMP = (0.10, 0.35)
ERP = 0.05
TERMINAL_G_FLOOR, TERMINAL_G_CEIL = 0.015, 0.025
CASH_FLOOR_PCT = 0.02

# Synthetic-rating spread table. Constants here because the engine does no I/O;
# a parity test asserts these match methodology.yaml's spread_table exactly.
SPREAD_TABLE: list[tuple[float, str, float]] = [
    (12.5, "AAA/AA", 0.0070),
    (9.5, "A+", 0.0090),
    (7.5, "A", 0.0105),
    (6.0, "A-", 0.0120),
    (4.5, "BBB+", 0.0150),
    (3.5, "BBB", 0.0180),
    (3.0, "BB+", 0.0250),
    (0.0, "BB and below", 0.0400),
]


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


def _pct_of_revenue(window: list[FiscalPeriod], item: str) -> float:
    return _mean([p.value(item, 0.0) / p.value("revenue") for p in window])


def _prior_pairs(history: FinancialHistory) -> list[tuple[FiscalPeriod, FiscalPeriod]]:
    """(prior, current) pairs, most recent min(3, available) — for ratios that
    need a beginning balance."""
    pairs = list(zip(history.periods, history.periods[1:], strict=False))
    return pairs[-min(3, len(pairs)):]


def derive_assumptions(history: FinancialHistory, market: MarketInputs) -> Assumptions:
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
        "max(1.5%, min(2.5%, 10Y)) — floored so distorted-rate regimes don't "
        "mechanically crush the valuation", unit="rate")
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

    # ── toggles ────────────────────────────────────────────────────────────
    add("midyear", True, "Mid-year discounting (Gordon TV at N−0.5; exit TV at N "
                         "— deliberate asymmetry)", unit="flag")
    add("sbc_addback", False, "SBC stays a real expense (owner-confirmed); toggle "
                              "re-adds it as non-cash", unit="flag")
    add("kd_synthetic", True, "Synthetic-rating Kd default; toggle = embedded "
                              "legacy rate", unit="flag")
    add("beta_adjusted", True, "Blume adjustment on (⅔β + ⅓)", unit="flag")

    return Assumptions(fields=a, cost_structure=cs)


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
    "terminal_growth": (0.0, 0.06, "terminal g within [0%, 6%]"),
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
}


def apply_overrides(assumptions: Assumptions, overrides: dict[str, float | bool]
                    ) -> Assumptions:
    for name, value in overrides.items():
        if name not in assumptions.fields:
            raise InvalidAssumptionError(name, "not an assumption field", value)
        if name in _DOMAINS and not isinstance(value, bool):
            lo, hi, label = _DOMAINS[name]
            if not (lo <= float(value) <= hi):
                raise InvalidAssumptionError(name, label, value)
        assumptions.fields[name].override = value
    return assumptions
