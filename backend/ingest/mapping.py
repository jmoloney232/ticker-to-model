"""Tag mapping: companyfacts -> canonical fiscal periods (spec 01 §5–6).

Chain policy: per (item, fiscal year), the first tag in the schema chain with a
fact wins; the winner is recorded as provenance. Missing values follow the item's
missing_rule — never a silent zero. Derive expressions in schema.yaml are
documentation; the executable versions live in DERIVERS below and test_schema
asserts the two sets stay in sync.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date

from .errors import (
    InsufficientHistoryError,
    MissingRequiredItemError,
    UnsupportedCurrencyError,
)
from .facts import (
    Selected,
    annual_durations,
    annual_instants,
    select_durations_by_fy,
    select_instant_at,
    units_present,
)
from .models import Fact, FiscalPeriod, IngestWarning
from .periods import duration_days, is_53_week
from .schema import Schema, SchemaItem

MIN_YEARS = 3

CASH_TAGS = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
)


@dataclass
class MappedHistory:
    periods: list[FiscalPeriod]                 # ascending fiscal years, gapless
    warnings: list[IngestWarning]
    ni_pairs: dict[int, tuple[float, float]]    # fy -> (NetIncomeLoss, ProfitLoss), for H3
    alt_cash: dict[int, float]                  # fy -> other cash-definition value, for H2
    prior_fy: int | None = None                 # fiscal year before the window (for ΔCash)
    prior_cash: float | None = None
    prior_alt_cash: float | None = None


class _Ctx:
    """Per-fiscal-year view handed to derivers."""

    def __init__(self, mapper: _Mapper, fy: int):
        self._m = mapper
        self.fy = fy
        self.fye = mapper.fye_map[fy]

    def fact(self, name: str) -> Fact | None:
        return self._m.values[self.fy].get(name)

    def val(self, name: str) -> float | None:
        f = self.fact(name)
        return f.value if f else None

    def val0(self, name: str) -> float:
        """Optional components default to 0 inside residuals (their own
        zero_logged warning still fires in the missing-rule pass)."""
        v = self.val(name)
        return 0.0 if v is None else v

    def tagged(self, name: str) -> Fact | None:
        f = self.fact(name)
        return f if f is not None and f.source == "tag" else None

    def raw_instant(self, tag: str) -> float | None:
        return self._m.raw_instant(tag, self.fye)

    def raw_duration(self, tag: str) -> float | None:
        return self._m.raw_duration(tag, self.fy)

    def warn(self, code: str, message: str, item: str | None = None) -> None:
        self._m.warnings.append(
            IngestWarning(code=code, message=message, fiscal_year=self.fy, item=item)
        )


# ── Derive registry (must match schema items with missing_rule: derive) ───────

def _d_shares_diluted_wa(c: _Ctx):
    return c.val("shares_basic_wa")

def _d_gross_profit(c: _Ctx):
    r, cogs = c.val("revenue"), c.val("cost_of_revenue")
    return None if r is None or cogs is None else r - cogs

def _d_selling_general_admin(c: _Ctx):
    sm = c.raw_duration("SellingAndMarketingExpense")
    ga = c.raw_duration("GeneralAndAdministrativeExpense")
    if sm is None and ga is None:
        return None
    return (sm or 0.0) + (ga or 0.0)

def _d_other_operating(c: _Ctx):
    gp, oi = c.val("gross_profit"), c.val("operating_income")
    if gp is None or oi is None:
        return None
    return gp - c.val0("research_and_development") - c.val0("selling_general_admin") - oi

def _d_other_nonoperating(c: _Ctx):
    pretax, oi = c.val("pretax_income"), c.val("operating_income")
    if pretax is None or oi is None:
        return None
    return pretax - oi + c.val0("interest_expense") - c.val0("interest_income")

def _d_short_term_debt(c: _Ctx):
    stb = c.raw_instant("ShortTermBorrowings")
    cp = c.raw_instant("CommercialPaper")
    # Current LTD: the AndCapitalLeaseObligations variant already includes finance
    # leases, so the two branches are alternatives, never summed (double count).
    ltd_cur = c.raw_instant("LongTermDebtCurrent")
    if ltd_cur is not None:
        cur = ltd_cur + (c.raw_instant("FinanceLeaseLiabilityCurrent") or 0.0)
    else:
        cur = c.raw_instant("LongTermDebtAndCapitalLeaseObligationsCurrent")
    if stb is None and cp is None and cur is None:
        return None
    return (stb or 0.0) + (cp or 0.0) + (cur or 0.0)

def _d_other_current_assets(c: _Ctx):
    tca = c.tagged("total_current_assets")
    if tca is None:
        return None
    resid = tca.value - (c.val0("cash_and_equivalents") + c.val0("short_term_investments")
                         + c.val0("accounts_receivable") + c.val0("inventory"))
    if resid < 0:
        c.warn("negative_residual",
               f"FY{c.fy}: other_current_assets residual is negative ({resid:,.0f}) — "
               "mapped current-asset components overlap the reported total.",
               item="other_current_assets")
    return resid

def _d_total_current_assets(c: _Ctx):
    c.warn("unclassified_bs",
           f"FY{c.fy}: AssetsCurrent not reported; total_current_assets derived by "
           "summing mapped components.", item="total_current_assets")
    return (c.val0("cash_and_equivalents") + c.val0("short_term_investments")
            + c.val0("accounts_receivable") + c.val0("inventory")
            + c.val0("other_current_assets"))

def _d_other_current_liabilities(c: _Ctx):
    tcl = c.tagged("total_current_liabilities")
    if tcl is None:
        return None
    resid = tcl.value - (c.val0("accounts_payable") + c.val0("accrued_liabilities")
                         + c.val0("short_term_debt") + c.val0("deferred_revenue_current")
                         + (c.raw_instant("OperatingLeaseLiabilityCurrent") or 0.0))
    if resid < 0:
        c.warn("negative_residual",
               f"FY{c.fy}: other_current_liabilities residual is negative ({resid:,.0f}).",
               item="other_current_liabilities")
    return resid

def _d_total_current_liabilities(c: _Ctx):
    c.warn("unclassified_bs",
           f"FY{c.fy}: LiabilitiesCurrent not reported; total_current_liabilities "
           "derived by summing mapped components.", item="total_current_liabilities")
    return (c.val0("accounts_payable") + c.val0("accrued_liabilities")
            + c.val0("short_term_debt") + c.val0("deferred_revenue_current")
            + c.val0("other_current_liabilities"))

def _d_other_noncurrent_assets(c: _Ctx):
    assets, tca = c.val("total_assets"), c.val("total_current_assets")
    if assets is None or tca is None:
        return None
    return assets - tca - (c.val0("ppe_net") + c.val0("goodwill") + c.val0("intangibles")
                           + c.val0("long_term_investments") + c.val0("operating_lease_rou"))

def _d_total_liabilities(c: _Ctx):
    le = c.tagged("total_liabilities_and_equity")
    if le is None:
        return None
    c.warn("derived_total_liabilities",
           f"FY{c.fy}: Liabilities not reported; derived from L&E minus equity.",
           item="total_liabilities")
    return le.value - (c.val0("stockholders_equity") + c.val0("noncontrolling_interest")
                       + c.val0("temporary_equity"))

def _d_other_noncurrent_liabilities(c: _Ctx):
    tl, tcl = c.val("total_liabilities"), c.val("total_current_liabilities")
    if tl is None or tcl is None:
        return None
    return tl - tcl - (c.val0("long_term_debt")
                       + (c.raw_instant("OperatingLeaseLiabilityNoncurrent") or 0.0)
                       + c.val0("deferred_tax_liabilities") + c.val0("pension_liability"))

def _d_total_liabilities_and_equity(c: _Ctx):
    tl = c.val("total_liabilities")
    if tl is None:
        return None
    return tl + (c.val0("stockholders_equity") + c.val0("noncontrolling_interest")
                 + c.val0("temporary_equity"))

def _d_working_capital_change(c: _Ctx):
    cfo, ni, da = c.val("cash_from_operations"), c.val("net_income"), c.val("d_and_a")
    if cfo is None or ni is None or da is None:
        return None
    # Residual view: also absorbs other non-cash adjustments — documented in schema.
    return cfo - ni - da - c.val0("stock_compensation") - c.val0("deferred_taxes_cf")

def _d_net_change_in_cash(c: _Ctx):
    cfo = c.val("cash_from_operations")
    cfi = c.val("cash_from_investing")
    cff = c.val("cash_from_financing")
    if cfo is None or cfi is None or cff is None:
        return None
    return cfo + cfi + cff + c.val0("fx_effect")


DERIVERS: list[tuple[str, callable]] = [
    ("shares_diluted_wa", _d_shares_diluted_wa),
    ("gross_profit", _d_gross_profit),
    ("selling_general_admin", _d_selling_general_admin),
    ("other_operating", _d_other_operating),
    ("other_nonoperating", _d_other_nonoperating),
    ("short_term_debt", _d_short_term_debt),
    ("other_current_assets", _d_other_current_assets),
    ("total_current_assets", _d_total_current_assets),
    ("other_current_liabilities", _d_other_current_liabilities),
    ("total_current_liabilities", _d_total_current_liabilities),
    ("other_noncurrent_assets", _d_other_noncurrent_assets),
    ("total_liabilities", _d_total_liabilities),
    ("other_noncurrent_liabilities", _d_other_noncurrent_liabilities),
    ("total_liabilities_and_equity", _d_total_liabilities_and_equity),
    ("working_capital_change", _d_working_capital_change),
    ("net_change_in_cash", _d_net_change_in_cash),
]


class _Mapper:
    def __init__(self, payload: dict, schema: Schema, anchor: str | None, ticker: str,
                 years: int):
        self.payload = payload
        self.schema = schema
        self.anchor = anchor
        self.ticker = ticker
        self.years = years
        self.warnings: list[IngestWarning] = []
        self.values: dict[int, dict[str, Fact]] = {}
        self.fye_map: dict[int, date] = {}
        self.meta_map: dict[int, Selected] = {}

    # ── raw accessors ──────────────────────────────────────────────────────
    def raw_instant(self, tag: str, fye: date) -> float | None:
        got = select_instant_at(
            annual_instants(self.payload, "us-gaap", tag, "USD"), fye)
        return got[0].value if got else None

    def raw_duration(self, tag: str, fy: int) -> float | None:
        sel = select_durations_by_fy(
            annual_durations(self.payload, "us-gaap", tag, "USD"), self.anchor)
        got = sel.get(fy)
        return got.value if got else None

    # ── chain resolution ───────────────────────────────────────────────────
    def chain_durations(self, item: SchemaItem) -> dict[int, tuple[str, Selected]]:
        out: dict[int, tuple[str, Selected]] = {}
        for ns, tag in item.namespaced_tags():
            sel = select_durations_by_fy(
                annual_durations(self.payload, ns, tag, item.unit), self.anchor)
            for fy, s in sel.items():
                out.setdefault(fy, (f"{ns}:{tag}", s))
        return out

    def chain_instant_at(self, item: SchemaItem, fye: date
                         ) -> tuple[str, Selected, bool] | None:
        for ns, tag in item.namespaced_tags():
            got = select_instant_at(
                annual_instants(self.payload, ns, tag, item.unit), fye)
            if got is not None:
                return f"{ns}:{tag}", got[0], got[1]
        return None

    # ── period determination (spec 01 §6) ──────────────────────────────────
    def determine_periods(self) -> list[int]:
        rev_item = self.schema.items["revenue"]
        rev = self.chain_durations(rev_item)
        if not rev:
            non_usd = sorted({
                u
                for ns, tag in rev_item.namespaced_tags()
                for u in units_present(self.payload, ns, tag)
                if u != "USD"
            })
            if non_usd:
                raise UnsupportedCurrencyError(self.ticker, non_usd)
            raise InsufficientHistoryError(self.ticker, 0, MIN_YEARS,
                                           "No annual revenue facts found in 10-K filings.")

        cfo = self.chain_durations(self.schema.items["cash_from_operations"])
        assets_item = self.schema.items["total_assets"]
        usable = sorted(
            fy for fy, (_, s) in rev.items()
            if fy in cfo and self.chain_instant_at(assets_item, s.end) is not None
        )
        if not usable:
            raise InsufficientHistoryError(
                self.ticker, 0, MIN_YEARS,
                "No fiscal year has revenue, operating cash flow, and total assets together.")

        # Most recent gapless run (spec 01: never interpolate across a gap).
        run = [usable[-1]]
        remaining = set(usable)
        while len(run) < self.years and (run[-1] - 1) in remaining:
            run.append(run[-1] - 1)
        run.reverse()
        if len(run) < MIN_YEARS:
            reason = (f"Only FY{run[0]}–FY{run[-1]} are consecutive; a gap exists at "
                      f"FY{run[0] - 1}." if len(usable) > len(run) else
                      "Fewer than three usable fiscal years were filed.")
            raise InsufficientHistoryError(self.ticker, len(run), MIN_YEARS, reason)
        if len(run) < self.years and len(usable) > len(run):
            self.warnings.append(IngestWarning(
                code="history_trimmed_at_gap",
                message=(f"History limited to FY{run[0]}–FY{run[-1]}: a gap at "
                         f"FY{run[0] - 1} cuts off older filed years (never "
                         "interpolated across)."),
            ))

        for fy in run:
            self.meta_map[fy] = rev[fy][1]
            self.fye_map[fy] = rev[fy][1].end
            self.values[fy] = {}
        return run

    # ── fact materialization ───────────────────────────────────────────────
    def _fact_from(self, tag: str, s: Selected) -> Fact:
        return Fact(
            value=s.value, unit=s.unit, tag=tag, source="tag",
            accession=s.accession, filed=s.filed, end=s.end,
            first_filed_value=s.first_filed_value,
            was_restated=s.was_restated,
            restatement_delta_pct=s.restatement_delta_pct,
        )

    def map_items(self, run: list[int]) -> None:
        for item in self.schema.items.values():
            if item.selection == "latest":
                continue
            if item.shape == "duration":
                mapped = self.chain_durations(item)
                for fy in run:
                    got = mapped.get(fy)
                    if got:
                        self.values[fy][item.name] = self._fact_from(got[0], got[1])
            else:
                for fy in run:
                    got = self.chain_instant_at(item, self.fye_map[fy])
                    if got:
                        tag, s, fuzzy = got
                        if fuzzy:
                            self.warnings.append(IngestWarning(
                                code="instant_date_fuzzy",
                                message=(f"FY{fy}: {item.name} balance date {s.end} does "
                                         f"not exactly match the fiscal year end "
                                         f"{self.fye_map[fy]}."),
                                fiscal_year=fy, item=item.name))
                        self.values[fy][item.name] = self._fact_from(tag, s)

    # ── post-chain adjustments (schema notes made executable) ──────────────
    def adjust(self, run: list[int]) -> None:
        for fy in run:
            v = self.values[fy]
            ctx = _Ctx(self, fy)

            ni = v.get("net_income")
            if ni is not None and ni.tag == "us-gaap:ProfitLoss":
                nci = v.get("nci_income")
                if nci is not None and nci.source == "tag":
                    v["net_income"] = dataclasses.replace(ni, value=ni.value - nci.value)
                else:
                    ctx.warn("profitloss_without_nci",
                             f"FY{fy}: net income sourced from ProfitLoss (includes NCI) "
                             "but no NCI income tag found; value may include NCI.",
                             item="net_income")

            se = v.get("stockholders_equity")
            if se is not None and se.tag.endswith(
                    "IncludingPortionAttributableToNoncontrollingInterest"):
                nci = v.get("noncontrolling_interest")
                if nci is not None and nci.source == "tag":
                    v["stockholders_equity"] = dataclasses.replace(
                        se, value=se.value - nci.value)
                else:
                    ctx.warn("equity_includes_nci",
                             f"FY{fy}: stockholders_equity sourced from the including-NCI "
                             "tag with no NCI tag to subtract.",
                             item="stockholders_equity")

            ap = v.get("accounts_payable")
            if ap is not None and ap.tag.endswith("AccountsPayableAndAccruedLiabilitiesCurrent"):
                accrued = v.get("accrued_liabilities")
                if accrued is not None and accrued.source == "tag":
                    v["accounts_payable"] = dataclasses.replace(
                        ap, value=ap.value - accrued.value)
                    ctx.warn("combined_ap_accrued",
                             f"FY{fy}: AP reported combined with accrued liabilities; "
                             "accrued subtracted out.", item="accounts_payable")
                else:
                    v["accrued_liabilities"] = Fact(0.0, "USD", ap.tag, "zero_logged",
                                                    end=self.fye_map[fy])
                    ctx.warn("combined_ap_accrued",
                             f"FY{fy}: AP and accrued reported as one combined tag; "
                             "accrued set to 0 to avoid double counting (DPO will "
                             "overstate).", item="accrued_liabilities")

            ltd = v.get("long_term_debt")
            fln = ctx.raw_instant("FinanceLeaseLiabilityNoncurrent")
            if ltd is not None and ltd.tag == "us-gaap:LongTermDebtNoncurrent" and fln:
                v["long_term_debt"] = dataclasses.replace(ltd, value=ltd.value + fln)
                ctx.warn("finance_lease_added",
                         f"FY{fy}: noncurrent finance lease liabilities ({fln:,.0f}) "
                         "added to long-term debt.", item="long_term_debt")
            elif ltd is None:
                total = ctx.raw_instant("LongTermDebt")
                if total is not None:
                    current = ctx.raw_instant("LongTermDebtCurrent") or 0.0
                    v["long_term_debt"] = Fact(total - current + (fln or 0.0), "USD",
                                               "derived", "derived", end=self.fye_map[fy])

            oll_cur = ctx.raw_instant("OperatingLeaseLiabilityCurrent")
            oll_non = ctx.raw_instant("OperatingLeaseLiabilityNoncurrent")
            if oll_cur is not None or oll_non is not None:
                v["operating_lease_liability"] = Fact(
                    (oll_cur or 0.0) + (oll_non or 0.0), "USD", "derived", "derived",
                    end=self.fye_map[fy])

            wc = v.get("working_capital_change")
            if wc is not None and wc.source == "tag":
                # IncreaseDecreaseInOperatingCapital: positive = WC build = cash use.
                # Canonical convention is cash impact (positive = inflow) -> negate.
                v["working_capital_change"] = dataclasses.replace(wc, value=-wc.value)

            # d_and_a composite fallback (schema derive note): filers like MSFT tag
            # Depreciation and AmortizationOfIntangibleAssets separately and never
            # the combined concept. Required item, so this runs before the check.
            if "d_and_a" not in v:
                dep = ctx.raw_duration("Depreciation")
                amort = ctx.raw_duration("AmortizationOfIntangibleAssets")
                if dep is not None:
                    v["d_and_a"] = Fact(dep + (amort or 0.0), "USD", "derived",
                                        "derived")

    # ── derivers + missing rules ───────────────────────────────────────────
    def derive_and_fill(self, run: list[int]) -> None:
        for fy in run:
            ctx = _Ctx(self, fy)
            for name, fn in DERIVERS:
                if name in self.values[fy]:
                    continue
                item = self.schema.items[name]
                result = fn(ctx)
                if result is not None:
                    end = self.fye_map[fy] if item.shape == "instant" else None
                    self.values[fy][name] = Fact(result, item.unit, "derived", "derived",
                                                 end=end)

        for fy in run:
            for item in self.schema.items.values():
                if item.selection == "latest" or item.name in self.values[fy]:
                    continue
                if item.required:
                    raise MissingRequiredItemError(self.ticker, item.name, fy,
                                                   list(item.tags))
                if item.missing_rule == "omit":
                    continue
                end = self.fye_map[fy] if item.shape == "instant" else None
                self.values[fy][item.name] = Fact(0.0, item.unit, "", "zero_logged",
                                                  end=end)
                self.warnings.append(IngestWarning(
                    code="unmapped_item",
                    message=(f"FY{fy}: {item.name} unmapped (tried: "
                             f"{', '.join(item.tags) or 'derivation only'}); treated as 0."),
                    fiscal_year=fy, item=item.name))

    # ── extras for validation ──────────────────────────────────────────────
    def restatement_warnings(self, run: list[int]) -> None:
        for fy in run:
            for name, f in self.values[fy].items():
                if f.was_restated:
                    self.warnings.append(IngestWarning(
                        code="restated",
                        message=(f"FY{fy}: {name} was restated — latest filing shows "
                                 f"{f.value:,.0f} vs {f.first_filed_value:,.0f} as first "
                                 f"filed ({f.restatement_delta_pct:.1%} change)."),
                        fiscal_year=fy, item=name,
                        detail={"delta_pct": f.restatement_delta_pct}))

    def build(self) -> MappedHistory:
        run = self.determine_periods()
        self.map_items(run)
        self.adjust(run)
        self.derive_and_fill(run)
        self.restatement_warnings(run)

        ni_pairs: dict[int, tuple[float, float]] = {}
        alt_cash: dict[int, float] = {}
        for fy in run:
            nil = self.raw_duration("NetIncomeLoss", fy)
            pl = self.raw_duration("ProfitLoss", fy)
            if nil is not None and pl is not None:
                ni_pairs[fy] = (nil, pl)
            chosen = self.values[fy]["cash_and_equivalents"].tag
            for tag in CASH_TAGS:
                if f"us-gaap:{tag}" != chosen:
                    alt = self.raw_instant(tag, self.fye_map[fy])
                    if alt is not None:
                        alt_cash[fy] = alt

        prior_fy = run[0] - 1
        prior_cash = prior_alt = None
        rev_all = self.chain_durations(self.schema.items["revenue"])
        if prior_fy in rev_all:
            prior_end = rev_all[prior_fy][1].end
            got = self.chain_instant_at(self.schema.items["cash_and_equivalents"], prior_end)
            if got:
                prior_cash = got[1].value
                for tag in CASH_TAGS:
                    if f"us-gaap:{tag}" != got[0]:
                        prior_alt = self.raw_instant(tag, prior_end)

        periods = []
        for fy in run:
            meta = self.meta_map[fy]
            income, balance, cashflow = {}, {}, {}
            for name, f in self.values[fy].items():
                stmt = self.schema.items[name].statement
                {"income": income, "balance": balance, "cashflow": cashflow}[stmt][name] = f
            periods.append(FiscalPeriod(
                fiscal_year=fy, start=meta.start, end=meta.end,
                duration_days=duration_days(meta.start, meta.end),
                is_53_week=is_53_week(meta.start, meta.end),
                income=income, balance=balance, cashflow=cashflow))

        for p in periods:
            if p.is_53_week:
                self.warnings.append(IngestWarning(
                    code="week53",
                    message=(f"FY{p.fiscal_year} is a 53-week year ({p.duration_days} "
                             "days) — growth vs adjacent years is inflated ~1.9%."),
                    fiscal_year=p.fiscal_year))

        return MappedHistory(
            periods=periods, warnings=self.warnings, ni_pairs=ni_pairs,
            alt_cash=alt_cash, prior_fy=prior_fy if prior_cash is not None else None,
            prior_cash=prior_cash, prior_alt_cash=prior_alt)


def map_history(payload: dict, schema: Schema, anchor: str | None, ticker: str,
                years: int = 5) -> MappedHistory:
    return _Mapper(payload, schema, anchor, ticker, years).build()
