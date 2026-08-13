"""Historical validation gate — checks H1–H7 (specs/07-validation.md).

Validation observes and reports; it never fixes. Tolerances are the named
constants below — no inline magic numbers elsewhere.
"""

from __future__ import annotations

from itertools import pairwise

from .mapping import MappedHistory
from .models import CheckResult, FiscalPeriod, ValidationReport

ABS_TOLERANCE = 1_000_000.0          # $1M
REL_TOLERANCE = 0.001                # 0.1% of total assets
RE_ROLLFORWARD_REL = 0.05            # H4: 5% of total equity


def _tol(assets: float | None) -> float:
    return max(ABS_TOLERANCE, REL_TOLERANCE * abs(assets or 0.0))


def _worst(per_period: dict[int, float]) -> float | None:
    return max(per_period.values(), default=None)


def _h1(periods: list[FiscalPeriod]) -> CheckResult:
    per, failed, ran = {}, False, False
    for p in periods:
        assets = p.value("total_assets")
        tl = p.balance.get("total_liabilities")
        le = p.balance.get("total_liabilities_and_equity")
        if tl is not None and tl.source == "tag":
            rhs = (tl.value + p.value("temporary_equity", 0.0)
                   + p.value("stockholders_equity", 0.0)
                   + p.value("noncontrolling_interest", 0.0))
        elif le is not None and le.source == "tag":
            rhs = le.value
        else:
            continue
        ran = True
        resid = abs(assets - rhs)
        per[p.fiscal_year] = resid
        if resid > _tol(assets):
            failed = True
    if not ran:
        return CheckResult("H1", "fail", "skipped",
                           detail="Neither Liabilities nor LiabilitiesAndStockholdersEquity "
                                  "was independently reported.")
    return CheckResult("H1", "fail", "fail" if failed else "pass",
                       magnitude=_worst(per), tolerance=_tol(periods[-1].value("total_assets")),
                       detail="Assets = Liabilities + temporary equity + equity + NCI",
                       per_period=per)


def _h2(periods: list[FiscalPeriod], m: MappedHistory) -> CheckResult:
    per, failed, ran = {}, False, False
    mismatch_notes = []
    for i, p in enumerate(periods):
        flows = (p.value("cash_from_operations") + p.value("cash_from_investing")
                 + p.value("cash_from_financing") + p.value("fx_effect", 0.0))
        tol = _tol(p.value("total_assets"))

        ncc = p.cashflow.get("net_change_in_cash")
        if ncc is not None and ncc.source == "tag":
            ran = True
            resid = abs(flows - ncc.value)
            per[p.fiscal_year] = max(per.get(p.fiscal_year, 0.0), resid)
            if resid > tol:
                failed = True

        if i > 0:
            prior_cash = periods[i - 1].value("cash_and_equivalents")
            prior_alt = m.alt_cash.get(periods[i - 1].fiscal_year)
        else:
            prior_cash, prior_alt = m.prior_cash, m.prior_alt_cash
        if prior_cash is None:
            continue
        ran = True
        resid = abs((p.value("cash_and_equivalents") - prior_cash) - flows)
        if resid > tol:
            alt_now = m.alt_cash.get(p.fiscal_year)
            if alt_now is not None and prior_alt is not None:
                alt_resid = abs((alt_now - prior_alt) - flows)
                if alt_resid <= tol:
                    mismatch_notes.append(
                        f"FY{p.fiscal_year}: ties only under the including-restricted-cash "
                        "definition (ASU 2016-18 definition mismatch, not a real break)")
                    per[p.fiscal_year] = max(per.get(p.fiscal_year, 0.0), alt_resid)
                    continue
            failed = True
        per[p.fiscal_year] = max(per.get(p.fiscal_year, 0.0), resid)

    if not ran:
        return CheckResult("H2", "fail", "skipped",
                           detail="No reported net-change-in-cash and no prior-year "
                                  "balance to difference against.")
    detail = "CFO + CFI + CFF + FX = change in cash"
    if mismatch_notes:
        detail += "; " + "; ".join(mismatch_notes)
    return CheckResult("H2", "fail", "fail" if failed else "pass",
                       magnitude=_worst(per),
                       tolerance=_tol(periods[-1].value("total_assets")),
                       detail=detail, per_period=per)


def _h3(periods: list[FiscalPeriod], m: MappedHistory) -> CheckResult:
    if not m.ni_pairs:
        return CheckResult("H3", "fail", "skipped",
                           detail="NetIncomeLoss and ProfitLoss never co-reported.")
    per, failed = {}, False
    for p in periods:
        pair = m.ni_pairs.get(p.fiscal_year)
        if pair is None:
            continue
        nil, pl = pair
        nci = p.value("nci_income", 0.0)
        resid = abs(nil + nci - pl)
        per[p.fiscal_year] = resid
        if resid > _tol(p.value("total_assets")):
            failed = True
    return CheckResult("H3", "fail", "fail" if failed else "pass",
                       magnitude=_worst(per),
                       tolerance=_tol(periods[-1].value("total_assets")),
                       detail="NetIncomeLoss + NCI income = ProfitLoss", per_period=per)


def _h4(periods: list[FiscalPeriod]) -> CheckResult:
    if any("retained_earnings" not in p.balance for p in periods):
        return CheckResult("H4", "warn", "skipped",
                           detail="Retained earnings not reported in every year.")
    per, warned = {}, False
    for prev, p in pairwise(periods):
        expected = (prev.value("retained_earnings") + p.value("net_income")
                    - p.value("dividends_paid", 0.0))
        resid = abs(p.value("retained_earnings") - expected)
        per[p.fiscal_year] = resid
        if resid > RE_ROLLFORWARD_REL * abs(p.value("stockholders_equity") or 1.0):
            warned = True
    return CheckResult("H4", "warn", "warn" if warned else "pass",
                       magnitude=_worst(per),
                       detail="RE_t ≈ RE_{t-1} + NI − dividends (soft: buyback retirement, "
                              "OCI reclassifications, and ASU adoptions hit RE directly)",
                       per_period=per)


def _h5(periods: list[FiscalPeriod]) -> CheckResult:
    per, warned, notes, ran = {}, False, [], False

    def check(fy: int, assets: float | None, label: str, reported: float, derived: float):
        nonlocal warned, ran
        ran = True
        resid = abs(reported - derived)
        per[fy] = max(per.get(fy, 0.0), resid)
        if resid > _tol(assets):
            warned = True
            notes.append(f"FY{fy} {label}: reported {reported:,.0f} vs derived {derived:,.0f}")

    for p in periods:
        assets = p.value("total_assets")
        gp = p.income.get("gross_profit")
        if gp is not None and gp.source == "tag":
            check(p.fiscal_year, assets, "gross profit",
                  gp.value, p.value("revenue") - p.value("cost_of_revenue"))
        tca = p.balance.get("total_current_assets")
        oca = p.balance.get("other_current_assets")
        if tca is not None and tca.source == "tag" and oca is not None and oca.source == "tag":
            check(p.fiscal_year, assets, "current assets", tca.value,
                  p.value("cash_and_equivalents", 0.0) + p.value("short_term_investments", 0.0)
                  + p.value("accounts_receivable", 0.0) + p.value("inventory", 0.0) + oca.value)
        tl = p.balance.get("total_liabilities")
        le = p.balance.get("total_liabilities_and_equity")
        if tl is not None and tl.source == "tag" and le is not None and le.source == "tag":
            check(p.fiscal_year, assets, "total liabilities", le.value,
                  tl.value + p.value("temporary_equity", 0.0)
                  + p.value("stockholders_equity", 0.0)
                  + p.value("noncontrolling_interest", 0.0))

    if not ran:
        return CheckResult("H5", "warn", "skipped",
                           detail="No redundantly reported aggregates to cross-check.")
    return CheckResult("H5", "warn", "warn" if warned else "pass",
                       magnitude=_worst(per),
                       detail="; ".join(notes) or "Reported aggregates match derived values",
                       per_period=per)


def _h6(periods: list[FiscalPeriod]) -> CheckResult:
    per: dict[int, float] = {}
    count = 0
    for p in periods:
        for f in list(p.income.values()) + list(p.balance.values()) + list(p.cashflow.values()):
            if f.was_restated:
                count += 1
                per[p.fiscal_year] = max(per.get(p.fiscal_year, 0.0),
                                         f.restatement_delta_pct or 0.0)
    if count == 0:
        return CheckResult("H6", "warn", "pass", detail="No restatements above 1%.")
    return CheckResult("H6", "warn", "warn", magnitude=_worst(per),
                       detail=f"{count} line item(s) restated by more than 1% "
                              "(latest-filed values in use)", per_period=per)


def _h7(periods: list[FiscalPeriod]) -> CheckResult:
    weeks53 = [p.fiscal_year for p in periods if p.is_53_week]
    if not weeks53:
        return CheckResult("H7", "info", "pass", detail="All years are 52-week/365-day.")
    return CheckResult("H7", "info", "warn",
                       detail="53-week fiscal year(s): "
                              + ", ".join(f"FY{y}" for y in weeks53)
                              + " — growth vs adjacent years inflated ~1.9%",
                       per_period={y: 1.0 for y in weeks53})


def validate_history(m: MappedHistory) -> ValidationReport:
    periods = m.periods
    return ValidationReport(results=[
        _h1(periods), _h2(periods, m), _h3(periods, m),
        _h4(periods), _h5(periods), _h6(periods), _h7(periods),
    ])
