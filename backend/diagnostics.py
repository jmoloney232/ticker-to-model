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


def run_ticker(ticker: str, edgar, provider) -> dict:
    history = build_financial_history(ticker, edgar)
    market = build_market_inputs(ticker, provider, as_of=VALUATION_DATE)
    m = build_model(history, market, valuation_date=VALUATION_DATE)
    price = market.price.value
    a = m.assumptions

    gordon = m.bridges["gordon"].value_per_share
    exit_ps = (m.bridges["exit_multiple"].value_per_share
               if "exit_multiple" in m.bridges else None)

    # convention sensitivities — information only, conventions unchanged
    sbc_on = build_model(history, market, valuation_date=VALUATION_DATE,
                         overrides={"sbc_addback": True}
                         ).bridges["gordon"].value_per_share
    rf = a.eff("risk_free")
    g_unc = min(rf, m.wacc.wacc - 0.005)       # remove the 2.5% ceiling only
    g_uncapped = (build_model(history, market, valuation_date=VALUATION_DATE,
                              overrides={"terminal_growth": g_unc}
                              ).bridges["gordon"].value_per_share
                  if g_unc > a.eff("terminal_growth") else gordon)
    lease_ps = (history.periods[-1].value("operating_lease_liability", 0.0)
                / a.eff("share_count"))        # bridge-only effect of leases-in-debt

    reverse = {f: {"derived": r.derived, "implied": r.implied, "status": r.status}
               for f, r in implied_all(history, market, VALUATION_DATE).items()}

    warn_codes = sorted({c for _, c, _ in m.all_warnings()
                         if c not in ("unmapped_item",)})
    return {
        "ticker": ticker, "sector": history.company.sic_description[:28],
        "price": price, "gordon": gordon, "exit": exit_ps,
        "gap_gordon": gordon / price - 1,
        "gap_exit": exit_ps / price - 1 if exit_ps else None,
        "wacc": m.wacc.wacc, "beta": m.wacc.beta_used,
        "implied_terminal_multiple": m.crosschecks.get("implied_exit_multiple"),
        "current_ev_ebitda": a.eff("exit_multiple"),
        "fy1_growth": a.eff("revenue_growth_fy1"),
        "cagr_uncapped": a.eff("revenue_cagr_uncapped"),
        "capex_pct": a.eff("capex_pct"),
        "sbc_addback_delta": sbc_on - gordon,
        "g_uncapped_delta": g_uncapped - gordon,
        "leases_in_debt_delta": -lease_ps,
        "reverse": reverse,
        "warnings": warn_codes,
        "cost_structure": history.cost_structure,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--tickers", nargs="*", default=None)
    args = parser.parse_args()

    cache = SqliteCache(".scan_cache.sqlite")
    edgar = EdgarClient(user_agent=os.environ["EDGAR_USER_AGENT"], cache=cache)
    provider = LadderedProvider(
        AlpacaClient(os.environ.get("ALPACA_API_KEY_ID", ""),
                     os.environ.get("ALPACA_API_SECRET_KEY", "")),
        FredClient(os.environ.get("FRED_API_KEY", "")), cache=cache)

    rows, failures = [], []
    for ticker in args.tickers or UNIVERSE:
        try:
            rows.append(run_ticker(ticker, edgar, provider))
            r = rows[-1]
            print(f"{ticker:<6} gordon {r['gordon']:>9.2f}  "
                  f"exit {r['exit'] or float('nan'):>9.2f}  "
                  f"price {r['price']:>9.2f}  gap_g {r['gap_gordon']:>+7.0%}")
        except (IngestError, MarketDataError, Exception) as exc:  # noqa: BLE001
            failures.append((ticker, type(exc).__name__, str(exc)[:140]))
            print(f"{ticker:<6} FAILED {type(exc).__name__}: {str(exc)[:110]}")

    gaps = [r["gap_gordon"] for r in rows]
    capex = [r["capex_pct"] for r in rows]
    growth = [r["fy1_growth"] for r in rows]
    print(f"\nn={len(rows)}  median gap_gordon="
          f"{sorted(gaps)[len(gaps) // 2]:+.0%}")
    print(f"corr(gap_gordon, capex_pct)  = {pearson(gaps, capex):+.3f}")
    print(f"corr(gap_gordon, fy1_growth) = {pearson(gaps, growth):+.3f}")

    if args.out:
        with open(args.out, "w") as fh:
            json.dump({"valuation_date": VALUATION_DATE.isoformat(),
                       "rows": rows, "failures": failures}, fh, indent=2)
        print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
