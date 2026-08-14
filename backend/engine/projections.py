"""Three-statement projections FY1–FYn, n ∈ {5, 7, 10} (specs/04-engine.md).

Built so the exported workbook needs no circular references: interest on
BEGINNING-of-period balances, cash is the plug. Cost lines are D&A-inclusive
as filed (EBIT = revenue − cost lines); memo D&A drives only CF/BS/EBITDA.
Every balance-sheet line has exactly one documented rule — held-flat lines are
a stated modeling choice, and the plug absorbs only the residual of stated
rules (owner decision). P1/P2 hold exactly by construction and are asserted
anyway (engine-bug tripwires).
"""

from __future__ import annotations

from datetime import timedelta

from ingest.models import FinancialHistory, FiscalPeriod

from .assumptions import gross_debt
from .models import Assumptions, ProjectedPeriod

# Horizon comes from the forecast_years assumption ({5, 7, 10}, default 5 —
# audit task 7); every fade reaches its terminal level in the final year.

# Noncurrent non-debt lines held flat at FY0 (owner decision, spec 04): no
# defensible history-derived growth rule exists for these in v1, and an
# explicit flat line is inspectable where a silent default is not.
FLAT_BALANCE_ITEMS = (
    "short_term_investments", "goodwill", "intangibles", "long_term_investments",
    "operating_lease_rou", "other_noncurrent_assets", "investments_combined_unsplit",
    "short_term_debt", "long_term_debt", "operating_lease_liability",
    "deferred_tax_liabilities", "pension_liability", "other_noncurrent_liabilities",
    "noncontrolling_interest", "preferred_equity", "temporary_equity",
)


def horizon(assumptions: Assumptions) -> int:
    return int(assumptions.eff("forecast_years"))


def growth_path(assumptions: Assumptions) -> list[float]:
    """Linear fade from FY1 growth to terminal g by the final forecast year
    (curved fade is a documented v1.1 candidate)."""
    g1 = assumptions.eff("revenue_growth_fy1")
    gt = assumptions.eff("terminal_growth")
    n = horizon(assumptions)
    return [g1 + i / (n - 1) * (gt - g1) for i in range(n)]


def tax_path(assumptions: Assumptions) -> list[float]:
    eff = assumptions.eff("effective_tax_fy1")
    marginal = assumptions.eff("marginal_tax")
    n = horizon(assumptions)
    return [eff + i / (n - 1) * (marginal - eff) for i in range(n)]


def _flat(fy0: FiscalPeriod, item: str) -> float:
    return fy0.value(item, 0.0)


def project(history: FinancialHistory, assumptions: Assumptions
            ) -> list[ProjectedPeriod]:
    fy0 = history.periods[-1]
    a = assumptions
    cs = assumptions.cost_structure
    growth = growth_path(a)
    taxes = tax_path(a)

    debt0 = gross_debt(fy0)
    da_on_ppe = a.has("da_pct_beginning_ppe")

    periods: list[ProjectedPeriod] = []
    prev_rev = fy0.value("revenue")
    prev_ppe = fy0.value("ppe_net", 0.0)
    prev_cash = fy0.value("cash_and_equivalents", 0.0)
    prev_sti = fy0.value("short_term_investments", 0.0)
    prev_equity = fy0.value("stockholders_equity", 0.0)
    prev_wc = _nwc_from_history(fy0)

    # FY0 enumeration residual: real filers' mapped components don't sum to
    # reported cash EXACTLY (mapping residuals within the H1 tolerance, already
    # validated and disclosed). Carried as an explicit flat line — visible in
    # every projected balance sheet, never silently absorbed by the plug — so
    # P1 and P2 both hold exactly against the reported FY0 cash anchor.
    carryforward = fy0.value("cash_and_equivalents", 0.0) - (
        _enumerated_liabilities(fy0) + _enumerated_equity(fy0)
        - _enumerated_assets_ex_cash(fy0))

    for i in range(horizon(a)):
        fy = fy0.fiscal_year + i + 1
        fye = fy0.end + timedelta(days=round(365.25 * (i + 1)))
        rev = prev_rev * (1 + growth[i])

        # ── income statement (cost lines D&A-inclusive; EBIT = rev − costs) ─
        income: dict[str, float] = {"revenue": rev}
        costs = 0.0
        if cs == "by_function":
            income["cost_of_revenue"] = a.eff("cogs_pct") * rev
            income["gross_profit"] = rev - income["cost_of_revenue"]
            costs += income["cost_of_revenue"]
        for name, key in (("research_and_development", "rnd_pct"),
                          ("selling_general_admin", "sga_pct"),
                          ("other_operating", "other_opex_pct"),
                          ("unclassified_costs", "unclassified_costs_pct")):
            income[name] = a.eff(key) * rev
            costs += income[name]
        ebit = rev - costs
        income["operating_income"] = ebit

        da = (a.eff("da_pct_beginning_ppe") * prev_ppe if da_on_ppe
              else a.eff("da_pct_revenue") * rev)
        interest_exp = a.eff("embedded_debt_rate") * debt0
        interest_inc = a.eff("interest_income_yield") * (prev_cash + prev_sti)
        pretax = ebit - interest_exp + interest_inc      # other non-operating = 0
        tax = taxes[i] * pretax
        ni = pretax - tax
        income.update({"interest_expense": interest_exp,
                       "interest_income": interest_inc,
                       "pretax_income": pretax, "income_tax": tax,
                       "net_income": ni})

        # ── balance sheet ───────────────────────────────────────────────────
        basis = income["cost_of_revenue"] if cs == "by_function" else costs
        # by_nature basis = total operating costs = rev − EBIT, matching the
        # historical ratio's denominator exactly (cost_basis in assumptions)
        balance: dict[str, float] = {
            "accounts_receivable": a.eff("dso") / 365 * rev,
            "inventory": a.eff("dio") / 365 * basis,
            "accounts_payable": a.eff("dpo") / 365 * basis,
            "other_current_assets": a.eff("oca_pct") * rev,
            "accrued_liabilities": a.eff("accrued_pct") * rev,
            "other_current_liabilities": a.eff("ocl_pct") * rev,
            "deferred_revenue_current": a.eff("defrev_pct") * rev,
        }
        capex = a.eff("capex_pct") * rev
        sbc = a.eff("sbc_pct") * rev
        ppe = prev_ppe + capex - da
        balance["ppe_net"] = ppe
        for item in FLAT_BALANCE_ITEMS:
            balance[item] = _flat(fy0, item)

        dividends = a.eff("payout_ratio") * max(ni, 0.0)
        equity = prev_equity + ni - dividends + sbc      # SBC credits equity
        balance["stockholders_equity"] = equity

        balance["unattributed_carryforward"] = carryforward
        liabilities = (balance["accounts_payable"] + balance["accrued_liabilities"]
                       + balance["other_current_liabilities"]
                       + balance["deferred_revenue_current"]
                       + balance["short_term_debt"] + balance["long_term_debt"]
                       + balance["operating_lease_liability"]
                       + balance["deferred_tax_liabilities"]
                       + balance["pension_liability"]
                       + balance["other_noncurrent_liabilities"]
                       + carryforward)
        equity_side = (equity + balance["noncontrolling_interest"]
                       + balance["preferred_equity"] + balance["temporary_equity"])
        assets_ex_cash = (balance["accounts_receivable"] + balance["inventory"]
                          + balance["other_current_assets"]
                          + balance["short_term_investments"] + ppe
                          + balance["goodwill"] + balance["intangibles"]
                          + balance["operating_lease_rou"]
                          + balance["long_term_investments"]
                          + balance["investments_combined_unsplit"]
                          + balance["other_noncurrent_assets"])
        cash = liabilities + equity_side - assets_ex_cash        # THE plug
        balance["cash_and_equivalents"] = cash
        balance["total_assets"] = assets_ex_cash + cash
        balance["total_liabilities"] = liabilities

        # ── cash flow (indirect; must tie exactly — P2) ─────────────────────
        wc = _nwc(balance)
        delta_nwc = wc - prev_wc
        cfo = ni + da + sbc - delta_nwc
        cfi = -capex
        cff = -dividends
        cashflow = {"net_income": ni, "d_and_a": da, "stock_compensation": sbc,
                    "working_capital_change": -delta_nwc, "cash_from_operations": cfo,
                    "capex": capex, "cash_from_investing": cfi,
                    "dividends_paid": dividends, "cash_from_financing": cff,
                    "net_change_in_cash": cfo + cfi + cff}

        # P1/P2 tripwires: exact by construction, asserted as engine-bug guards.
        # Tolerances are RELATIVE: at trillion-dollar scale, float ulp on the
        # plug arithmetic is ~1e-3 dollars — mathematically zero, and an
        # absolute tolerance here was a real bug (caught by the diagnostic
        # batch: 13 mega-cap filers false-asserted while fixtures passed by
        # rounding luck).
        assert abs(balance["total_assets"] - (liabilities + equity_side)) < 1e-9 * max(
            1.0, abs(balance["total_assets"])), "P1: BS does not balance"
        assert abs((cfo + cfi + cff) - (cash - prev_cash)) < 1e-6 * max(
            1.0, abs(cash)), "P2: CF does not tie to Δcash"

        periods.append(ProjectedPeriod(fiscal_year=fy, fye=fye, income=income,
                                       balance=balance, cashflow=cashflow))
        prev_rev, prev_ppe, prev_cash = rev, ppe, cash
        prev_sti = balance["short_term_investments"]
        prev_equity, prev_wc = equity, wc

    return periods


def _nwc(balance: dict[str, float]) -> float:
    """Operating NWC: excludes cash, ST investments, and all debt (spec 04)."""
    return (balance["accounts_receivable"] + balance["inventory"]
            + balance["other_current_assets"]
            - balance["accounts_payable"] - balance["accrued_liabilities"]
            - balance["other_current_liabilities"]
            - balance["deferred_revenue_current"])


def _nwc_from_history(p: FiscalPeriod) -> float:
    return (p.value("accounts_receivable", 0.0) + p.value("inventory", 0.0)
            + p.value("other_current_assets", 0.0)
            - p.value("accounts_payable", 0.0) - p.value("accrued_liabilities", 0.0)
            - p.value("other_current_liabilities", 0.0)
            - p.value("deferred_revenue_current", 0.0))


def _enumerated_liabilities(p: FiscalPeriod) -> float:
    return sum(p.value(i, 0.0) for i in (
        "accounts_payable", "accrued_liabilities", "other_current_liabilities",
        "deferred_revenue_current", "short_term_debt", "long_term_debt",
        "operating_lease_liability", "deferred_tax_liabilities",
        "pension_liability", "other_noncurrent_liabilities"))


def _enumerated_equity(p: FiscalPeriod) -> float:
    return sum(p.value(i, 0.0) for i in (
        "stockholders_equity", "noncontrolling_interest", "preferred_equity",
        "temporary_equity"))


def _enumerated_assets_ex_cash(p: FiscalPeriod) -> float:
    return sum(p.value(i, 0.0) for i in (
        "accounts_receivable", "inventory", "other_current_assets",
        "short_term_investments", "ppe_net", "goodwill", "intangibles",
        "operating_lease_rou", "long_term_investments",
        "investments_combined_unsplit", "other_noncurrent_assets"))
