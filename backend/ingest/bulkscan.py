"""Bulk mapping-hardening scan — NOT part of the test suite.

Assembles a broad ticker set against live EDGAR and reports, per ticker:
years assembled, validation result, coverage shares, top unmapped tags, and
all warnings (including PL plausibility checks). Findings feed a human review
of the tag chains — this script never edits schema.yaml.

Usage:
    EDGAR_USER_AGENT="TickerToModel/0.1 (you@example.com)" \
        python -m ingest.bulkscan            # scan the proposed list
    python -m ingest.bulkscan AAPL GE ...    # scan specific tickers

Raw EDGAR payloads cache to .scan_cache.sqlite (gitignored via *.sqlite);
re-runs are offline. Respect for the EDGAR rate limit is built into EdgarClient.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

from .assemble import build_financial_history
from .cache import SqliteCache
from .edgar import EdgarClient
from .errors import IngestError

SCAN_CACHE = Path(__file__).parent.parent / ".scan_cache.sqlite"

# Deliberately awkward filers, spread across sectors. Two expected rejections
# (UNH, AMT) verify the SIC boundary on real data.
PROPOSED_TICKERS: dict[str, str] = {
    # Tech / dual class / custom-tag-prone
    "AAPL": "52/53-week FY ending last Saturday of September; CP borrower; buyback-heavy",
    "GOOGL": "dual share class; giant investment portfolio (non-marketable equity tags)",
    "META": "dual class; heavy finance+operating lease mix",
    "AMZN": "lease-heavy both types; content assets; famously idiosyncratic CF statement",
    "NVDA": "52/53-week FY ending late January (retail-style calendar in tech)",
    "AVGO": "52/53-week early-November FYE; VMware deal = massive acquired intangibles",
    "CRM": "January 31 FYE; serial acquirer; large deferred revenue",
    "TSLA": "custom extension tags; historically redeemable NCI; leases both sides",
    # Industrials / pension / captive finance arms
    "GE": "pension-heavy; discontinued operations; spin-offs and restatement history",
    "BA": "huge pension; negative equity; long-cycle inventory accounting",
    "CAT": "captive finance subsidiary (Cat Financial) inside a non-financial SIC",
    "F": "Ford Credit captive; pension; NCI",
    "DE": "captive finance; 52/53-week late-October FYE",
    "DAL": "pension + lease-heavy; loyalty-program liabilities",
    "VZ": "pension/OPEB; enormous debt stack + operating leases",
    # Retail 52/53-week January FYEs
    "WMT": "January 31 FYE; scale stress-test",
    "TGT": "52/53-week FY ending Saturday nearest Jan 31",
    "HD": "52/53-week; negative-equity years from buybacks",
    # Consumer / pharma
    "PG": "June 30 FYE",
    "PEP": "52/53-week FY ending late December; bottler NCI",
    "MCD": "negative equity; franchisor asset mix (owned PP&E + leases)",
    "SBUX": "52/53-week late-September FYE; negative equity; card/deferred revenue",
    "JNJ": "52/53-week FY ending Sunday nearest Dec 31 — looks calendar, is not",
    "ABBV": "intangibles + goodwill dominate total assets",
    # Energy / utility
    "XOM": "equity-method investments; noncontrolling interests",
    "NEE": "regulated-utility tags; NCI (NextEra Partners); temporary equity",
    # Media
    "DIS": "52/53-week late-September FYE; redeemable NCI (temporary equity)",
    # Expected rejections — verify the SIC boundary
    "UNH": "SIC 6324 managed care: inside the 6300-6499 insurance rejection range",
    "AMT": "REIT (SIC 6798): verifies REIT rejection on real data",
}


def scan(tickers: list[str]) -> None:
    client = EdgarClient(
        user_agent=os.environ.get("EDGAR_USER_AGENT", ""),
        cache=SqliteCache(SCAN_CACHE),
    )
    rows = []
    details = []
    for ticker in tickers:
        try:
            h = build_financial_history(ticker, client)
        except IngestError as exc:
            rows.append((ticker, "-", f"REJECTED/{type(exc).__name__}", "-", "-", "-"))
            details.append((ticker, [f"{type(exc).__name__}: {exc.user_message}"]))
            continue

        fys = f"FY{h.periods[0].fiscal_year}-{h.periods[-1].fiscal_year}"
        cov = h.coverage
        cov_str = (f"A{cov.assets_named_share:.0%}/L{cov.liabilities_named_share:.0%}"
                   + (f"/E{cov.expenses_named_share:.0%}"
                      if cov.expenses_named_share is not None else "/E-"))
        pl_warns = [r for r in h.validation.results
                    if r.check_id.startswith("PL") and r.status == "warn"]
        h_warns = [r for r in h.validation.results
                   if not r.check_id.startswith("PL") and r.status == "warn"]
        warn_counts = Counter(w.code for w in h.warnings)
        rows.append((
            ticker, fys, h.validation.overall, cov_str,
            ",".join(r.check_id for r in pl_warns) or "-",
            ",".join(r.check_id for r in h_warns) or "-",
        ))

        lines = []
        for r in pl_warns:
            lines.append(f"{r.check_id}: {r.detail}")
        for r in h_warns:
            lines.append(f"{r.check_id}: {r.detail[:160]}")
        zero_logged = sorted({w.item for w in h.warnings
                              if w.code == "unmapped_item" and w.item})
        if zero_logged:
            lines.append(f"zero_logged items: {', '.join(zero_logged)}")
        top = ", ".join(f"{u.tag}=${u.value/1e9:.1f}B" for u in cov.top_unmapped[:6])
        lines.append(f"top unmapped tags: {top}")
        notable = {k: v for k, v in warn_counts.items() if k != "unmapped_item"}
        if notable:
            lines.append(f"other warnings: {dict(notable)}")
        details.append((ticker, lines))

    print(f"\n{'ticker':<7}{'years':<15}{'validation':<32}{'coverage':<16}"
          f"{'PL warns':<14}H warns")
    print("-" * 100)
    for r in rows:
        print(f"{r[0]:<7}{r[1]:<15}{r[2]:<32}{r[3]:<16}{r[4]:<14}{r[5]}")

    print("\n" + "=" * 100)
    for ticker, lines in details:
        if lines:
            print(f"\n### {ticker}")
            for line in lines:
                print(f"  - {line}")


if __name__ == "__main__":
    requested = [t.upper() for t in sys.argv[1:]] or list(PROPOSED_TICKERS)
    scan(requested)
