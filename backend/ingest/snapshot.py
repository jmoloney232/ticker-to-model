"""Fixture snapshot capture (spec 01, How tested; fixtures README).

Usage:
    EDGAR_USER_AGENT="TickerToModel/0.1 (you@example.com)" \
        python -m ingest.snapshot MSFT KO COST KHC JPM

Writes to backend/tests/fixtures/: company_tickers.json.gz, manifest.json, and per
ticker {submissions,companyfacts}.json.gz. companyfacts is pruned to keep the
repo small while staying real data:
  - us-gaap facts: 10-K / 10-K/A forms only (annual scope)
  - dei facts: all forms kept (cover-page shares need the latest 10-Q)
  - units: USD, shares, USD/shares only
Tags left with no facts are dropped. Refresh deliberately, never automatically.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
from pathlib import Path

from .cache import NullCache
from .edgar import EdgarClient

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"
KEEP_FORMS = {"10-K", "10-K/A"}
KEEP_UNITS = {"USD", "shares", "USD/shares"}


def prune_companyfacts(payload: dict) -> dict:
    out = {k: v for k, v in payload.items() if k != "facts"}
    out["facts"] = {}
    for ns, tags in payload.get("facts", {}).items():
        kept_tags = {}
        for tag, node in tags.items():
            kept_units = {}
            for unit, facts in node.get("units", {}).items():
                if unit not in KEEP_UNITS:
                    continue
                kept = (facts if ns == "dei"
                        else [f for f in facts if f.get("form") in KEEP_FORMS])
                if kept:
                    kept_units[unit] = kept
            if kept_units:
                kept_tags[tag] = {**{k: v for k, v in node.items() if k != "units"},
                                  "units": kept_units}
        if kept_tags:
            out["facts"][ns] = kept_tags
    return out


def _write_gz(path: Path, payload: dict) -> int:
    body = gzip.compress(json.dumps(payload, separators=(",", ":")).encode())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return len(body)


def main(tickers: list[str]) -> None:
    user_agent = os.environ.get("EDGAR_USER_AGENT", "")
    client = EdgarClient(user_agent=user_agent, cache=NullCache())

    tickers_payload, _ = client.get_company_tickers()
    size = _write_gz(FIXTURES_DIR / "company_tickers.json.gz", tickers_payload)
    print(f"company_tickers.json.gz  {size/1024:,.0f} KB")

    manifest_path = FIXTURES_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for ticker in tickers:
        ticker = ticker.upper()
        cik = client.resolve_cik(ticker)
        manifest[ticker] = cik
        tdir = FIXTURES_DIR / ticker.lower()

        submissions, _ = client.get_submissions(cik)
        # submissions carries a large filing index; metadata is all ingest needs.
        slim = {k: v for k, v in submissions.items() if k != "filings"}
        s1 = _write_gz(tdir / "submissions.json.gz", slim)

        facts, _ = client.get_companyfacts(cik)
        pruned = prune_companyfacts(facts)
        s2 = _write_gz(tdir / "companyfacts.json.gz", pruned)
        print(f"{ticker:<5} CIK {cik:>10}  submissions {s1/1024:>5,.0f} KB  "
              f"companyfacts {s2/1024:>7,.0f} KB")

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"manifest.json            {len(manifest)} tickers")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python -m ingest.snapshot TICKER [TICKER ...]")
    main(sys.argv[1:])
