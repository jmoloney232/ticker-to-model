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

# H2 materiality (owner-approved 2026-08-13): an unreconciled cash residual
# below BOTH legs is quantified and disclosed, not fatal — the auditor's
# treatment of an immaterial difference. 1% of revenue is the top of the
# standard audit materiality range for revenue-benchmarked entities; the
# gross-flows leg catches residuals that are small against revenue but large
# against the actual cash movement (calibrated on the 29-ticker scan: passes
# AMZN/TSLA/F/DIS at ≤0.73% rev / ≤3.86% flows; still fails GE's FY2022
# spin-year break at 1.27% of revenue).
H2_MATERIALITY_REV = 0.01            # of the year's revenue
H2_MATERIALITY_FLOWS = 0.05          # of the year's |CFO| + |CFI| + |CFF|


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
    """Cash flow ties to the change in cash.

    Four distinguishable per-year dispositions (`outcomes`), never collapsed
    into one warning type (owner decisions, hardening items 2 and this round's
    materiality item):
      tie          — flows tie to a cash delta or the reported net change
                     within tolerance
      definitional — ties only under a broader cash definition (restricted
                     cash / disposal groups), or the filer's reported net
                     change ties while our narrower BS cash delta does not
                     (verified WMT); a definition mismatch, not a break
      immaterial   — an unreconciled residual survives every basis but is
                     below BOTH materiality legs (H2_MATERIALITY_REV of
                     revenue and H2_MATERIALITY_FLOWS of gross flows);
                     quantified per year in dollars and percentages and
                     disclosed — the auditor's treatment, not a free pass
                     (verified AMZN/TSLA/F/DIS: FX / restricted-cash
                     presentation quirks of $0.1–1.3B against hundreds of
                     billions of revenue)
      fail         — the residual exceeds a materiality leg: a real break
                     (verified GE FY2022, the HealthCare-spin year)

    The materiality test covers both residual measures — the Δcash side and
    the internal |flows − reported net change| side — because on the verified
    filers the same FX/restricted-cash quirk breaks both by the same amount;
    a materially inconsistent CF statement still fails on either.
    `per_period` records what remains unreconciled after the best accepted
    basis: ≤ tolerance for tie/definitional years, the disclosed unexplained
    amount for immaterial/fail years.
    """
    per: dict[int, float] = {}
    outcomes: dict[int, str] = {}
    ran = False
    hard_fail = False
    def_notes, imm_notes, fail_notes = [], [], []
    for i, p in enumerate(periods):
        fy = p.fiscal_year
        flows = (p.value("cash_from_operations") + p.value("cash_from_investing")
                 + p.value("cash_from_financing") + p.value("fx_effect", 0.0))
        gross = (abs(p.value("cash_from_operations"))
                 + abs(p.value("cash_from_investing"))
                 + abs(p.value("cash_from_financing")))
        tol = _tol(p.value("total_assets"))

        residuals = []                  # every basis we could reconcile against
        ncc = p.cashflow.get("net_change_in_cash")
        r_ncc = None
        if ncc is not None and ncc.source == "tag":
            ran = True
            r_ncc = abs(flows - ncc.value)
            residuals.append(r_ncc)
        excess_ncc = r_ncc if r_ncc is not None and r_ncc > tol else 0.0

        if i > 0:
            prior_cash = periods[i - 1].value("cash_and_equivalents")
            prior_alt = m.alt_cash.get(periods[i - 1].fiscal_year)
        else:
            prior_cash, prior_alt = m.prior_cash, m.prior_alt_cash

        excess_delta = 0.0
        definitional = None
        if prior_cash is not None:
            ran = True
            r_delta = abs((p.value("cash_and_equivalents") - prior_cash) - flows)
            residuals.append(r_delta)
            if r_delta > tol:
                alt_now = m.alt_cash.get(fy)
                r_alt = None
                if alt_now is not None and prior_alt is not None:
                    r_alt = abs((alt_now - prior_alt) - flows)
                    residuals.append(r_alt)
                if r_alt is not None and r_alt <= tol:
                    definitional = ("ties under the broader cash definition "
                                    "(restricted cash / disposal groups — ASU "
                                    "2016-18 style definition mismatch, not a break)")
                elif r_ncc is not None and r_ncc <= tol:
                    definitional = ("the filer's reported net change ties to its "
                                    "flows, but the balance-sheet cash line uses "
                                    "a narrower definition than the CF total "
                                    "(definition mismatch, not a break)")
                else:
                    excess_delta = min(r for r in (r_delta, r_alt) if r is not None)

        if not residuals:
            continue                    # nothing to reconcile this year

        unexplained = max(excess_ncc, excess_delta)
        per[fy] = unexplained if unexplained > 0.0 else min(residuals)
        if unexplained == 0.0:
            if definitional is not None:
                outcomes[fy] = "definitional"
                def_notes.append(f"FY{fy}: {definitional}")
            else:
                outcomes[fy] = "tie"
            continue
        rev = p.value("revenue")
        quantified = (f"FY{fy}: ${unexplained/1e9:.2f}B unreconciled = "
                      f"{unexplained/rev:.2%} of revenue, "
                      f"{unexplained/gross:.2%} of gross cash flows")
        if (unexplained <= H2_MATERIALITY_REV * rev
                and unexplained <= H2_MATERIALITY_FLOWS * gross):
            outcomes[fy] = "immaterial"
            imm_notes.append(quantified)
        else:
            outcomes[fy] = "fail"
            hard_fail = True
            fail_notes.append(quantified + " — exceeds the materiality threshold "
                              f"({H2_MATERIALITY_REV:.0%} of revenue and "
                              f"{H2_MATERIALITY_FLOWS:.0%} of gross flows)")

    if not ran:
        return CheckResult("H2", "fail", "skipped",
                           detail="No reported net-change-in-cash and no prior-year "
                                  "balance to difference against.")
    detail = "CFO + CFI + CFF + FX = change in cash"
    if def_notes:
        detail += "; DEFINITIONAL: " + "; ".join(def_notes)
    if imm_notes:
        detail += "; IMMATERIAL: " + "; ".join(imm_notes)
    if fail_notes:
        detail += "; MATERIAL: " + "; ".join(fail_notes)
    status = ("fail" if hard_fail
              else "warn" if def_notes or imm_notes else "pass")
    return CheckResult("H2", "fail", status,
                       magnitude=_worst(per),
                       tolerance=_tol(periods[-1].value("total_assets")),
                       detail=detail, per_period=per, outcomes=outcomes)


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
            # Share/EPS-unit recasts are split adjustments (owner decision,
            # item 7) — labeled split_adjustment in warnings, not restatements.
            if f.was_restated and f.unit not in ("shares", "USD/shares"):
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
        *plausibility_checks(periods, m),
    ])


# ── Plausibility checks PL1–PL8 (spec 07) ────────────────────────────────────
#
# A different failure class from H1–H7: arithmetic tie-outs cannot see
# MISCLASSIFICATION, because residual buckets keep the statements balanced no
# matter where a value lands (the KHC long-term-debt gap balanced perfectly).
# These checks look for asymmetric presence — an item that implies another
# item exists, where the other resolved to zero. Always warnings, never
# errors: they flag combinations for human review. Rules are one-directional
# on purpose; the reverse implication usually has legitimate cases.

# "Zero" for a balance that resolved via zero_logged or a genuine nil.
_ZERO = 1.0


def _gross_debt(p: FiscalPeriod) -> float:
    return p.value("short_term_debt", 0.0) + p.value("long_term_debt", 0.0)


def _pl(check_id: str, label: str, flagged: dict[int, float], detail_ok: str,
        detail_bad: str) -> CheckResult:
    if not flagged:
        return CheckResult(check_id, "warn", "pass", detail=detail_ok)
    fys = ", ".join(f"FY{y}" for y in sorted(flagged))
    return CheckResult(check_id, "warn", "warn", magnitude=_worst(flagged),
                       detail=f"{label} in {fys}: {detail_bad}",
                       per_period=flagged)


def _pl1(periods: list[FiscalPeriod]) -> CheckResult:
    # Paying material interest implies borrowings. One-directional: debt with
    # no interest expense is NOT flagged (zero-coupon debt, capitalized
    # interest, fresh issuance). Known review-level false positives: interest
    # on uncertain tax positions or supplier financing tagged as
    # InterestExpense — usually immaterial, hence the tolerance floor.
    flagged = {p.fiscal_year: p.value("interest_expense", 0.0) for p in periods
               if p.value("interest_expense", 0.0) > _tol(p.value("total_assets"))
               and _gross_debt(p) <= _ZERO}
    return _pl("PL1", "interest_expense without any debt", flagged,
               "Interest expense is matched by debt on the balance sheet.",
               "material interest expense while short_term_debt + long_term_debt "
               "= 0 — paying interest implies borrowings; check the debt chains")


def _pl2(periods: list[FiscalPeriod]) -> CheckResult:
    # D&A or capex implies a depreciable/amortizable asset base. Goodwill is
    # excluded (not amortized). All three bases must be zero to warn, so an
    # asset-light software filer amortizing only intangibles stays quiet.
    flagged = {}
    for p in periods:
        activity = max(p.value("d_and_a", 0.0), p.value("capex", 0.0))
        base = (p.value("ppe_net", 0.0) + p.value("intangibles", 0.0)
                + p.value("operating_lease_rou", 0.0))
        if activity > _tol(p.value("total_assets")) and base <= _ZERO:
            flagged[p.fiscal_year] = activity
    return _pl("PL2", "D&A/capex without an asset base", flagged,
               "D&A and capex are matched by PP&E/intangible/ROU balances.",
               "depreciation or capex reported while PP&E, intangibles, and ROU "
               "assets are all 0 — check the asset chains")


def _pl3(periods: list[FiscalPeriod], cost_structure: str) -> CheckResult:
    # Selling something costs something — for filers that PRESENT costs by
    # function. by_nature filers (VZ, DAL, MCD — verified) have no COGS concept
    # at all, so this rule is gated on the explicit cost_structure field
    # (owner decision, item 4) rather than firing on every one of them.
    if cost_structure == "by_nature":
        return CheckResult("PL3", "warn", "skipped",
                           detail="Costs presented by nature — no COGS concept "
                                  "(cost_structure: by_nature).")
    flagged = {p.fiscal_year: p.value("revenue", 0.0) for p in periods
               if p.value("revenue", 0.0) > _tol(p.value("total_assets"))
               and p.value("cost_of_revenue", 0.0) <= 0.0}
    return _pl("PL3", "revenue without cost of revenue", flagged,
               "Revenue is matched by a cost of revenue.",
               "material revenue with cost_of_revenue <= 0 — check the COGS chain")


def _pl4(periods: list[FiscalPeriod], m: MappedHistory) -> CheckResult:
    # Repaying or issuing debt in financing activities implies debt on some
    # balance sheet. Known false positive: commercial paper issued and fully
    # repaid intra-year never shows at an FYE — one reason this is warn-only.
    if any(_gross_debt(p) > _ZERO for p in periods):
        return CheckResult("PL4", "warn", "pass",
                           detail="Debt flows are matched by balance-sheet debt.")
    flagged = {p.fiscal_year: max(p.value("debt_issued", 0.0),
                                  p.value("debt_repaid", 0.0))
               for p in periods
               if max(p.value("debt_issued", 0.0),
                      p.value("debt_repaid", 0.0)) > _tol(p.value("total_assets"))}
    return _pl("PL4", "debt flows without balance-sheet debt", flagged,
               "No debt flows, no debt — consistent.",
               "debt issued/repaid in financing while no debt ever appears on "
               "the balance sheet — check the debt chains (or intra-year CP churn)")


def _pl5(periods: list[FiscalPeriod], m: MappedHistory) -> CheckResult:
    # Lease cost implies a lease liability — but only post-ASC 842 (effective
    # for fiscal years beginning after 2018-12-15); before that, operating
    # leases were legitimately off balance sheet. Guarded to periods ending
    # 2020 onward.
    lease_cost = (m.probes or {}).get("lease_cost", {})
    flagged = {}
    for p in periods:
        if p.end.year < 2020:
            continue
        cost = lease_cost.get(p.fiscal_year, 0.0)
        if (cost > _tol(p.value("total_assets"))
                and p.value("operating_lease_liability", 0.0) <= _ZERO
                and p.value("operating_lease_rou", 0.0) <= _ZERO):
            flagged[p.fiscal_year] = cost
    return _pl("PL5", "lease cost without lease balances", flagged,
               "Lease cost is matched by lease balances (or predates ASC 842).",
               "operating lease cost reported while lease liability and ROU "
               "asset are both 0 — check the lease chains")


def _pl6(periods: list[FiscalPeriod], m: MappedHistory) -> CheckResult:
    # Tax expense implies tax-related balances somewhere: a deferred position
    # or a payable. Highest false-positive risk of the set — many filers fold
    # taxes payable into accrued liabilities — which is exactly why this is a
    # review-level warning, not an error.
    probes = m.probes or {}
    dta = probes.get("deferred_tax_assets", {})
    payable = probes.get("taxes_payable", {})
    flagged = {}
    for p in periods:
        balances = (p.value("deferred_tax_liabilities", 0.0)
                    + dta.get(p.fiscal_year, 0.0) + payable.get(p.fiscal_year, 0.0))
        if (p.value("income_tax", 0.0) > _tol(p.value("total_assets"))
                and balances <= _ZERO):
            flagged[p.fiscal_year] = p.value("income_tax", 0.0)
    return _pl("PL6", "tax expense without tax balances", flagged,
               "Tax expense is matched by deferred/payable tax balances.",
               "material tax expense with no deferred tax or taxes-payable "
               "balances — check the tax chains (payables may hide in accrued)")


def _pl7(periods: list[FiscalPeriod]) -> CheckResult:
    # ASC 842 books the ROU asset and lease liability together; one without
    # the other is almost always a mapping gap, not economics. (Impairment can
    # shrink the ROU asset, so only a hard zero on one side flags.)
    flagged = {}
    for p in periods:
        rou = p.value("operating_lease_rou", 0.0)
        liab = p.value("operating_lease_liability", 0.0)
        tol = _tol(p.value("total_assets"))
        if (rou > tol and liab <= _ZERO) or (liab > tol and rou <= _ZERO):
            flagged[p.fiscal_year] = max(rou, liab)
    return _pl("PL7", "one-sided ROU/lease-liability", flagged,
               "ROU assets and lease liabilities appear together.",
               "an ROU asset or operating lease liability appears without its "
               "counterpart — ASC 842 books them together; check the lease chains")


def _pl8(periods: list[FiscalPeriod]) -> CheckResult:
    # Accrual revenue almost always leaves receivables at year end. Flag only
    # when AR is zero in EVERY period — a persistent zero for a revenue-
    # generating filer suggests an AR tag outside the chain, not an all-cash
    # business. Single-year zeros stay quiet (securitizations, year-end sales).
    tol_latest = _tol(periods[-1].value("total_assets"))
    if all(p.value("revenue", 0.0) > tol_latest for p in periods) and all(
            p.value("accounts_receivable", 0.0) <= _ZERO for p in periods):
        flagged = {p.fiscal_year: p.value("revenue", 0.0) for p in periods}
        return _pl("PL8", "revenue without receivables", flagged,
                   "", "revenue in every year with accounts receivable = 0 in "
                       "every year — likely an unchained AR tag")
    return CheckResult("PL8", "warn", "pass",
                       detail="Receivables present for a revenue-generating filer.")


def plausibility_checks(periods: list[FiscalPeriod], m: MappedHistory) -> list[CheckResult]:
    return [_pl1(periods), _pl2(periods), _pl3(periods, m.cost_structure),
            _pl4(periods, m), _pl5(periods, m), _pl6(periods, m), _pl7(periods),
            _pl8(periods)]
