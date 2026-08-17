"""WACC build-up (specs/04-engine.md). Every intermediate is an output.

Invariant P3 (structural + tested): GROSS debt in the weights; net debt
appears only in the EV→equity bridge. Terminal NOPAT and after-tax Kd use the
same marginal rate — asserted, because mismatching them is the classic silent
bug.
"""

from __future__ import annotations

from ingest.models import FinancialHistory
from market.models import MarketInputs

from .assumptions import gross_debt, rating_for_coverage
from .models import Assumptions, WaccBuild


def build_wacc(history: FinancialHistory, market: MarketInputs,
               assumptions: Assumptions) -> WaccBuild:
    a = assumptions
    fy0 = history.periods[-1]
    rf = a.eff("risk_free")
    erp = a.eff("erp")
    marginal = a.eff("marginal_tax")

    beta_field = a.fields["beta"]
    if beta_field.override is not None:
        beta_used, beta_source = beta_field.override, "override"
    elif beta_field.preset_value is not None:
        beta_used = beta_field.preset_value
        beta_source = f"preset:{beta_field.preset_name}"
    elif market.beta is None:
        beta_used, beta_source = 1.0, "fallback_1.0"
    elif a.eff("beta_adjusted"):
        beta_used, beta_source = market.beta.adjusted, "blume"
    else:
        beta_used, beta_source = market.beta.raw, "raw"
    ke = rf + beta_used * erp

    coverage = a.eff("coverage_ratio")
    rating, spread = rating_for_coverage(coverage)
    synthetic_kd = rf + spread
    if a.eff("kd_synthetic"):
        kd_method, kd_pretax = "synthetic", synthetic_kd
    else:
        embedded = a.eff("embedded_debt_rate")
        if embedded > 0:
            kd_method, kd_pretax = "embedded", embedded
        else:                       # no observable coupon → synthetic, disclosed
            kd_method, kd_pretax = "synthetic (embedded unavailable)", synthetic_kd
    kd_after = kd_pretax * (1 - marginal)
    # terminal NOPAT uses a.eff("marginal_tax") too — same field, asserted here
    assert kd_after == kd_pretax * (1 - a.eff("marginal_tax"))

    market_cap = market.price.value * a.eff("share_count")
    debt = gross_debt(fy0)                       # GROSS book debt — invariant P3
    total = market_cap + debt
    we, wd = market_cap / total, debt / total

    # Terminal beta convergence (owner-approved 2026-08-17): β fades linearly
    # from beta_used (year 1) to terminal_beta (final explicit year); the
    # stable period runs at terminal_beta. Only the equity-risk component
    # converges — weights, Kd, rf, ERP are held (weights to avoid
    # circularity; synthetic Kd is already a forward-looking marginal rate).
    # year_rates[0] == wacc exactly: the path NESTS the single-rate model.
    beta_t = (a.eff("terminal_beta") if a.has("terminal_beta")
              else beta_used)
    n = int(a.eff("forecast_years")) if a.has("forecast_years") else 1
    year_betas = tuple(
        beta_used + (i / (n - 1) if n > 1 else 1.0) * (beta_t - beta_used)
        for i in range(n))
    year_rates = tuple(we * (rf + b * erp) + wd * kd_after
                       for b in year_betas)
    terminal_wacc = we * (rf + beta_t * erp) + wd * kd_after

    return WaccBuild(
        risk_free=rf, erp=erp, beta_used=beta_used, beta_source=beta_source,
        cost_of_equity=ke, coverage=coverage, rating=rating, spread=spread,
        kd_method=kd_method, kd_pretax=kd_pretax, kd_after_tax=kd_after,
        marginal_tax=marginal, market_cap=market_cap, gross_debt=debt,
        weight_equity=we, weight_debt=wd, wacc=we * ke + wd * kd_after,
        terminal_beta=beta_t, terminal_wacc=terminal_wacc,
        year_betas=year_betas, year_rates=year_rates,
    )
