"""Diagnostic batch across the supported universe — NOT part of the app.

Research, not tuning (owner framing): the market price is a comparison point,
never ground truth. This script measures systematic behavior — it changes
nothing. One fixed valuation date keeps the batch reproducible; EDGAR and
market payloads cache to .scan_cache.sqlite so re-runs are offline.

Usage:
    set -a; source ../.env; set +a
    python -m diagnostics [--out results.json] [--tickers MSFT KO ...]
    python -m diagnostics --bias        # two-arm structural-bias panels
    python -m diagnostics --levels      # level decomposition: how much of
                                        # the median gap each global constant
                                        # explains, with the discriminating-
                                        # power panel per hypothetical arm
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date

from engine.dcf import build_model
from engine.reverse import implied_all
from ingest.assemble import build_financial_history
from ingest.cache import SqliteCache
from ingest.edgar import EdgarClient
from ingest.errors import IngestError
from market.alpaca import AlpacaClient
from market.assemble import build_market_inputs
from market.errors import MarketDataError
from market.fred import FredClient
from market.provider import LadderedProvider

VALUATION_DATE = date(2026, 8, 14)          # fixed across the batch

# The supported universe: every filer that builds cleanly in the final scan,
# plus the committed fixtures. (GE/DE/XOM/NEE/UNH/AMT/JPM are excluded by
# their own gates — that exclusion working is phase 1's result, not this one's.)
UNIVERSE = [
    "MSFT", "AAPL", "GOOGL", "META", "AMZN", "NVDA", "AVGO", "CRM", "TSLA",
    "BA", "CAT", "F", "DAL", "VZ", "WMT", "TGT", "HD", "PG", "PEP", "KO",
    "COST", "MCD", "SBUX", "JNJ", "ABBV", "DIS", "KHC",
]


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return cov / (vx * vy) ** 0.5 if vx > 0 and vy > 0 else float("nan")


def run_ticker(ticker: str, edgar, provider, profile: str | None = "auto") -> dict:
    history = build_financial_history(ticker, edgar)
    market = build_market_inputs(ticker, provider, as_of=VALUATION_DATE)
    m = build_model(history, market, valuation_date=VALUATION_DATE,
                    profile=profile)
    price = market.price.value
    a = m.assumptions

    gordon = (m.bridges["gordon"].value_per_share
              if "gordon" in m.bridges else None)
    exit_ps = (m.bridges["exit_multiple"].value_per_share
               if "exit_multiple" in m.bridges else None)

    # convention sensitivities — information only, conventions unchanged;
    # None when the Gordon leg reports its honest unavailable state
    sbc_on = g_uncapped = gordon
    if gordon is not None:
        sbc_on = build_model(history, market, valuation_date=VALUATION_DATE,
                             overrides={"sbc_addback": True}
                             ).bridges["gordon"].value_per_share
        rf = a.eff("risk_free")
        g_unc = min(rf, m.wacc.wacc - 0.005)   # remove the 2.5% ceiling only
        if g_unc > a.eff("terminal_growth"):
            g_uncapped = build_model(history, market,
                                     valuation_date=VALUATION_DATE,
                                     overrides={"terminal_growth": g_unc}
                                     ).bridges["gordon"].value_per_share
    lease_ps = (history.periods[-1].value("operating_lease_liability", 0.0)
                / a.eff("share_count"))        # bridge-only effect of leases-in-debt

    reverse = {f: {"derived": r.derived, "implied": r.implied, "status": r.status}
               for f, r in implied_all(history, market, VALUATION_DATE).items()}

    warn_codes = sorted({c for _, c, _ in m.all_warnings()
                         if c not in ("unmapped_item",)})
    return {
        "ticker": ticker, "sector": history.company.sic_description[:28],
        "profile": a.profile.tag if a.profile else None,
        "price": price, "gordon": gordon, "exit": exit_ps,
        "gap_gordon": gordon / price - 1 if gordon is not None else None,
        "gap_exit": exit_ps / price - 1 if exit_ps else None,
        "wacc": m.wacc.wacc, "beta": m.wacc.beta_used,
        "implied_terminal_multiple": m.crosschecks.get("implied_exit_multiple"),
        "current_ev_ebitda": a.eff("exit_multiple"),
        "fy1_growth": a.eff("revenue_growth_fy1"),
        "cagr_uncapped": a.eff("revenue_cagr_uncapped"),
        "capex_pct": a.eff("capex_pct"),
        "sbc_addback_delta": sbc_on - gordon if gordon is not None else None,
        "g_uncapped_delta": g_uncapped - gordon if gordon is not None else None,
        "leases_in_debt_delta": -lease_ps,
        "reverse": reverse,
        "warnings": warn_codes,
        "cost_structure": history.cost_structure,
    }


def bias_panel(rows: list[dict], label: str) -> dict:
    """The structural-bias measurement (owner item 4, 2026-08-16): is the
    gap to market predictable from company characteristics? Correlations
    toward zero are success; a smaller median gap is NOT success."""
    valued = [r for r in rows if r["gap_gordon"] is not None]
    gaps = sorted(r["gap_gordon"] for r in valued)
    n = len(gaps)
    median = gaps[n // 2] if n % 2 else (gaps[n // 2 - 1] + gaps[n // 2]) / 2
    q1, q3 = gaps[n // 4], gaps[(3 * n) // 4]
    out = {"label": label, "n": n, "median_gap": median,
           "iqr": (q1, q3)}
    for char in ("wacc", "beta", "fy1_growth"):
        xs = [r[char] for r in valued]
        out[f"corr_{char}"] = pearson([r["gap_gordon"] for r in valued], xs)
    print(f"\n[{label}]  n={n}  median gap {median:+.0%}  "
          f"IQR [{q1:+.0%}, {q3:+.0%}]")
    print(f"  corr(gap, WACC)       = {out['corr_wacc']:+.3f}")
    print(f"  corr(gap, beta)       = {out['corr_beta']:+.3f}")
    print(f"  corr(gap, FY1 growth) = {out['corr_fy1_growth']:+.3f}")
    return out


# ── Level decomposition (owner item 1, 2026-08-17) ───────────────────────────
# The bias rounds measured whether the gap is PREDICTABLE from company
# characteristics; this measures the LEVEL — how much of the median gap each
# global constant explains, individually and in combination. Hypothetical
# arms only: every arm is an override run, no convention changes. The market
# price stays a comparison point, never a target — arms are chosen from
# published methodology alternatives, never from what closes the gap.

TAIL = 0.10          # a tail is "populated" when gaps beyond ±10% exist there


def power_panel(rows: list[dict], label: str, key: str = "gap") -> dict:
    """Discriminating-power metric (owner spec): a model that reads below
    market on nearly every name gives a ranking but no threshold. Reported
    per arm: above/below split, median, IQR, and whether both tails hold
    names beyond ±10%."""
    gaps = sorted(r[key] for r in rows if r.get(key) is not None)
    n = len(gaps)
    if n == 0:
        return {"label": label, "n": 0}
    median = gaps[n // 2] if n % 2 else (gaps[n // 2 - 1] + gaps[n // 2]) / 2
    q1, q3 = gaps[n // 4], gaps[(3 * n) // 4]
    above = sum(1 for g in gaps if g > 0)
    hi = sum(1 for g in gaps if g >= TAIL)
    lo = sum(1 for g in gaps if g <= -TAIL)
    out = {"label": label, "n": n, "median_gap": median, "iqr": (q1, q3),
           "above": above, "share_above": above / n,
           "tail_hi": hi, "tail_lo": lo}
    print(f"  {label:<26} median {median:+.0%}  IQR [{q1:+.0%},{q3:+.0%}] "
          f"width {q3 - q1:.0%}  above {above}/{n} ({above / n:.0%})  "
          f"tails +{hi}/-{lo}")
    return out


def _levels_ticker(ticker: str, edgar, provider) -> dict | None:
    """Base model plus every hypothetical arm for one filer. Returns None
    when the filer doesn't build or Gordon is honestly unavailable."""
    from engine.dcf import build_model as bm
    history = build_financial_history(ticker, edgar)
    market = build_market_inputs(ticker, provider, as_of=VALUATION_DATE)

    def gordon(overrides=None):
        m = bm(history, market, valuation_date=VALUATION_DATE,
               overrides=overrides or None)
        return (m, m.bridges["gordon"].value_per_share
                if "gordon" in m.bridges else None)

    base, base_ps = gordon()
    if base_ps is None:
        return None
    price = market.price.value
    a = base.assumptions
    rf = a.eff("risk_free")
    primary = a.profile.primary

    # The g-ceiling arm lifts the 2.5% house cap to the published g ≤ 10Y
    # ceiling for MATURE filers only: compounders already default to rf
    # (no-op), and a declining filer's g is anchored to its own trajectory
    # deliberately — lifting it would undo a different, correct rule.
    def g_lift(w_t: float) -> dict:
        target = min(rf, w_t - 0.005)
        if primary == "mature" and target > a.eff("terminal_growth"):
            return {"terminal_growth": target}
        return {}

    ARMS: dict[str, dict] = {
        "erp_433": {"erp": 0.0433},
        "g_ceiling_rf": g_lift(base.wacc.terminal_wacc),
        "sbc_addback": {"sbc_addback": True},
        "tax_21": {"marginal_tax": 0.21},
        "no_reinv_haircut": {"terminal_roic": 2.0},
        "capex_fade_all": {"capex_fade": True},
    }
    # ERP grid for the item-2 effects table (values argued in the proposal
    # from published methodology, never chosen for their gap effect)
    for name, erp in (("erp_400", 0.040), ("erp_460", 0.046),
                      ("erp_550", 0.055)):
        ARMS[name] = {"erp": erp}
    # the compounding pair: lower discount rate x higher terminal growth —
    # g must respect the ERP arm's OWN terminal WACC
    probe, _ = gordon({"erp": 0.0433})
    pair = {"erp": 0.0433, **g_lift(probe.wacc.terminal_wacc)}
    ARMS["erp_g_pair"] = pair
    ARMS["stack"] = {**pair, "sbc_addback": True, "marginal_tax": 0.21,
                     "terminal_roic": 2.0, "capex_fade": True}

    row = {"ticker": ticker, "price": price, "profile": a.profile.tag,
           "gap": base_ps / price - 1, "base_ps": base_ps}
    for name, ov in ARMS.items():
        _, ps = gordon(ov) if ov else (None, base_ps)
        row[name] = ps / price - 1 if ps is not None else None

    # EPV surface: maintenance capex = D&A is the constant; the published
    # alternative is maintenance = depreciation only (amortization of
    # acquired intangibles is not a cash reinvestment need). EV scales by
    # (nopat + amort0)/nopat under identical two-phase timing — computed
    # arithmetically, no engine change.
    epv = base.bridges.get("epv")
    fy0 = history.periods[-1]
    nopat = (fy0.value("revenue") * a.eff("epv_margin")
             * (1 - a.eff("marginal_tax")))
    if epv is not None and epv.value_per_share is not None:
        amort0 = fy0.value("amortization_intangibles", 0.0)
        row["gap_epv"] = epv.value_per_share / price - 1
        if nopat > 0:
            dps = amort0 * (epv.enterprise_value / nopat) / a.eff("share_count")
            row["epv_maint_dep_only"] = (epv.value_per_share + dps) / price - 1

    # Expensed R&D (missing feature, estimate only): 5y straight-line
    # capitalization. ΔEBIT0 = R&D0 − mean(history) ≈ amortization lag on a
    # growing program. EPV face: earnings-power understated by the growth
    # portion of R&D → uplift = ΔEBIT0·(1−t) capitalized on EPV's own
    # timing. DCF face: FCFF changes only by the tax-timing term
    # −t·(R&D−amort) — capitalization is mostly reclassification there.
    rnd = [p.value("research_and_development", 0.0) for p in history.periods]
    rev0 = history.periods[-1].value("revenue")
    if rnd[-1] / rev0 >= 0.05:
        d_ebit = rnd[-1] - sum(rnd) / len(rnd)
        row["rnd_pct_rev"] = rnd[-1] / rev0
        row["rnd_d_ebit"] = d_ebit
        t = a.eff("marginal_tax")
        if epv is not None and epv.value_per_share is not None and nopat > 0:
            up = (d_ebit * (1 - t) * (epv.enterprise_value / nopat)
                  / a.eff("share_count"))
            row["rnd_epv_uplift_ps"] = up
            row["rnd_epv_uplift_vs_price"] = up / price
        # per-year FCFF flow effect (not a PV): the DCF face of
        # capitalization is this small negative tax-timing term
        row["rnd_dcf_tax_timing_flow_ps"] = -t * d_ebit / a.eff("share_count")
    return row


def run_levels(tickers, edgar, provider, out_path=None) -> None:
    rows, failures = [], []
    for t in tickers:
        try:
            r = _levels_ticker(t, edgar, provider)
            if r is None:
                failures.append((t, "gordon unavailable"))
            else:
                rows.append(r)
                print(f"{t:<6} base {r['gap']:+.0%}  stack {r['stack']:+.0%}")
        except (IngestError, MarketDataError, Exception) as exc:  # noqa: BLE001
            failures.append((t, f"{type(exc).__name__}: {str(exc)[:90]}"))
            print(f"{t:<6} FAILED {type(exc).__name__}: {str(exc)[:90]}")

    print(f"\n== Level decomposition, n={len(rows)} "
          f"(fail/unavailable: {len(failures)}) ==")
    panels = {"base": power_panel(rows, "base (shipped defaults)")}
    order = ["erp_400", "erp_433", "erp_460", "erp_550", "g_ceiling_rf",
             "sbc_addback", "tax_21", "no_reinv_haircut", "capex_fade_all",
             "erp_g_pair", "stack"]
    for arm in order:
        panels[arm] = power_panel(rows, arm, key=arm)

    # additivity: does the stack equal the sum of its parts?
    singles = ["erp_433", "g_ceiling_rf", "sbc_addback", "tax_21",
               "no_reinv_haircut", "capex_fade_all"]
    adds = []
    for r in rows:
        if any(r.get(s) is None for s in singles + ["stack"]):
            continue
        sum_parts = sum(r[s] - r["gap"] for s in singles)
        adds.append({"ticker": r["ticker"], "sum_parts": sum_parts,
                     "stack_delta": r["stack"] - r["gap"]})
    if adds:
        ms = sorted(a["sum_parts"] for a in adds)
        mt = sorted(a["stack_delta"] for a in adds)
        print(f"\n  additivity: median sum-of-parts {ms[len(ms) // 2]:+.0%} "
              f"vs median stack delta {mt[len(mt) // 2]:+.0%} "
              f"(super-additive when stack > sum)")

    print("\n== EPV surface ==")
    panels["epv_base"] = power_panel(rows, "EPV base (maint = D&A)",
                                     key="gap_epv")
    panels["epv_dep_only"] = power_panel(rows, "EPV maint = dep only",
                                         key="epv_maint_dep_only")

    print("\n== Expensed R&D (estimate, no build) ==")
    for r in sorted(rows, key=lambda x: -x.get("rnd_pct_rev", 0)):
        if "rnd_epv_uplift_vs_price" in r:
            print(f"  {r['ticker']:<6} R&D {r['rnd_pct_rev']:.0%} of rev  "
                  f"ΔEBIT0 {r['rnd_d_ebit'] / 1e9:+.2f}B  EPV uplift "
                  f"{r['rnd_epv_uplift_ps']:+.2f}/sh "
                  f"({r['rnd_epv_uplift_vs_price']:+.0%} of price)")

    if out_path:
        with open(out_path, "w") as fh:
            json.dump({"valuation_date": VALUATION_DATE.isoformat(),
                       "rows": rows, "panels": panels,
                       "additivity": adds, "failures": failures}, fh, indent=1)
        print(f"written: {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--tickers", nargs="*", default=None)
    parser.add_argument("--bias", action="store_true",
                        help="run profiles-off AND profiles-on arms and "
                             "report the structural-bias panels")
    parser.add_argument("--levels", action="store_true",
                        help="level decomposition: contribution of each "
                             "global constant to the median gap, with "
                             "discriminating-power panels per arm")
    args = parser.parse_args()

    cache = SqliteCache(".scan_cache.sqlite")
    edgar = EdgarClient(user_agent=os.environ["EDGAR_USER_AGENT"], cache=cache)
    provider = LadderedProvider(
        AlpacaClient(os.environ.get("ALPACA_API_KEY_ID", ""),
                     os.environ.get("ALPACA_API_SECRET_KEY", "")),
        FredClient(os.environ.get("FRED_API_KEY", "")), cache=cache)

    if args.levels:
        run_levels(args.tickers or UNIVERSE, edgar, provider,
                   out_path=args.out)
        return 0

    rows, base_rows, failures = [], [], []
    for ticker in args.tickers or UNIVERSE:
        try:
            rows.append(run_ticker(ticker, edgar, provider))
            if args.bias:
                base_rows.append(run_ticker(ticker, edgar, provider,
                                            profile=None))
            r = rows[-1]
            gg = (f"{r['gap_gordon']:>+7.0%}" if r["gap_gordon"] is not None
                  else "  unavl")
            print(f"{ticker:<6} gordon {r['gordon'] if r['gordon'] is not None else float('nan'):>9.2f}  "
                  f"exit {r['exit'] or float('nan'):>9.2f}  "
                  f"price {r['price']:>9.2f}  gap_g {gg}  "
                  f"{r['profile'] or '-'}")
        except (IngestError, MarketDataError, Exception) as exc:  # noqa: BLE001
            failures.append((ticker, type(exc).__name__, str(exc)[:140]))
            print(f"{ticker:<6} FAILED {type(exc).__name__}: {str(exc)[:110]}")

    valued = [r for r in rows if r["gap_gordon"] is not None]
    gaps = [r["gap_gordon"] for r in valued]
    capex = [r["capex_pct"] for r in valued]
    growth = [r["fy1_growth"] for r in valued]
    print(f"\nn={len(rows)}  median gap_gordon="
          f"{sorted(gaps)[len(gaps) // 2]:+.0%}")
    print(f"corr(gap_gordon, capex_pct)  = {pearson(gaps, capex):+.3f}")
    print(f"corr(gap_gordon, fy1_growth) = {pearson(gaps, growth):+.3f}")

    panels = {}
    if args.bias:
        panels["before"] = bias_panel(base_rows, "profiles OFF (before)")
        panels["after"] = bias_panel(rows, "profiles ON (after)")
        comp = {r["ticker"] for r in rows
                if r["profile"] and r["profile"].startswith("compounder")}
        panels["compounders_before"] = bias_panel(
            [r for r in base_rows if r["ticker"] in comp],
            "compounder cohort, profiles OFF")
        panels["compounders_after"] = bias_panel(
            [r for r in rows if r["ticker"] in comp],
            "compounder cohort, profiles ON")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"valuation_date": VALUATION_DATE.isoformat(),
                       "rows": rows, "base_rows": base_rows,
                       "bias_panels": panels or None,
                       "failures": failures}, fh, indent=2)
        print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
