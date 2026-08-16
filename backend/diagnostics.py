"""Diagnostic batch across the supported universe — NOT part of the app.

Research, not tuning (owner framing): the market price is a comparison point,
never ground truth. This script measures systematic behavior — it changes
nothing. One fixed valuation date keeps the batch reproducible; EDGAR and
market payloads cache to .scan_cache.sqlite so re-runs are offline.

Usage:
    set -a; source ../.env; set +a
    python -m diagnostics [--out results.json] [--tickers MSFT KO ...]
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--tickers", nargs="*", default=None)
    parser.add_argument("--bias", action="store_true",
                        help="run profiles-off AND profiles-on arms and "
                             "report the structural-bias panels")
    args = parser.parse_args()

    cache = SqliteCache(".scan_cache.sqlite")
    edgar = EdgarClient(user_agent=os.environ["EDGAR_USER_AGENT"], cache=cache)
    provider = LadderedProvider(
        AlpacaClient(os.environ.get("ALPACA_API_KEY_ID", ""),
                     os.environ.get("ALPACA_API_SECRET_KEY", "")),
        FredClient(os.environ.get("FRED_API_KEY", "")), cache=cache)

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
