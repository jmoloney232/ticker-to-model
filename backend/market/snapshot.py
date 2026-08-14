"""Capture committed market snapshots (last-resort tier + test fixtures).

Usage:
    ALPACA_API_KEY_ID=... ALPACA_API_SECRET_KEY=... FRED_API_KEY=... \
        python -m market.snapshot MSFT KO COST KHC

Always also captures the benchmark (SPY) and DGS10. Writes gzipped JSON to
backend/tests/fixtures/market/ and merges manifest.json (never overwrites other
entries). Refreshed deliberately, never automatically — snapshots are reviewed
fixtures, not a rolling cache.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
from datetime import timedelta

from .alpaca import AlpacaClient
from .assemble import BENCHMARK
from .fred import FredClient
from .provider import BARS_WINDOW_DAYS, SNAPSHOT_DIR, market_today


def capture(symbols: list[str]) -> None:
    alpaca = AlpacaClient(os.environ.get("ALPACA_API_KEY_ID", ""),
                          os.environ.get("ALPACA_API_SECRET_KEY", ""))
    fred = FredClient(os.environ.get("FRED_API_KEY", ""))
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    today = market_today()
    start = today - timedelta(days=BARS_WINDOW_DAYS)

    manifest_path = SNAPSHOT_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    for symbol in dict.fromkeys([*symbols, BENCHMARK]):    # de-dupe, keep order
        bars = alpaca.daily_bars(symbol, start, today)
        payload = {"symbol": symbol, "captured": today.isoformat(),
                   "start": start.isoformat(), "end": today.isoformat(),
                   "bars": [[b.day.isoformat(), b.close] for b in bars]}
        out = SNAPSHOT_DIR / f"{symbol.lower()}_bars.json.gz"
        out.write_bytes(gzip.compress(json.dumps(payload).encode()))
        manifest[symbol] = {"bars": len(bars), "captured": today.isoformat()}
        print(f"{symbol}: {len(bars)} bars -> {out.name}")

    value, as_of = fred.dgs10()
    (SNAPSHOT_DIR / "dgs10.json.gz").write_bytes(gzip.compress(json.dumps(
        {"value": value, "date": as_of.isoformat(),
         "captured": today.isoformat()}).encode()))
    manifest["DGS10"] = {"value": value, "date": as_of.isoformat(),
                         "captured": today.isoformat()}
    print(f"DGS10: {value:.4f} as of {as_of}")

    _freeze_msft_beta(manifest)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def _freeze_msft_beta(manifest: dict) -> None:
    """Freeze MSFT's beta from the just-captured bars — the regression test
    against accidental methodology drift (spec 03, How tested)."""
    msft = SNAPSHOT_DIR / "msft_bars.json.gz"
    spy = SNAPSHOT_DIR / f"{BENCHMARK.lower()}_bars.json.gz"
    if not (msft.exists() and spy.exists()):
        return
    from .beta import compute_beta
    from .provider import _decode_bars

    bars = _decode_bars(json.loads(gzip.decompress(msft.read_bytes())))
    bench = _decode_bars(json.loads(gzip.decompress(spy.read_bytes())))
    b = compute_beta("MSFT", bars, bench, bars[0].day, bars[-1].day)
    (SNAPSHOT_DIR / "msft_beta_frozen.json").write_text(json.dumps(
        {"raw": b.raw, "adjusted": b.adjusted, "n_obs": b.n_obs,
         "r_squared": b.r_squared, "window": [b.window_start.isoformat(),
                                              b.window_end.isoformat()]},
        indent=2) + "\n")
    manifest["MSFT_BETA"] = {"raw": round(b.raw, 6), "n_obs": b.n_obs}
    print(f"MSFT beta frozen: raw={b.raw:.4f} adj={b.adjusted:.4f} n={b.n_obs}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python -m market.snapshot SYMBOL [SYMBOL ...]")
    capture([s.upper() for s in sys.argv[1:]])
