"""CLI tests — the phase 2 deliverable renders everything the owner listed,
fully offline against committed fixtures."""

from __future__ import annotations

import json
from datetime import date

import pytest
from test_engine import GOLDEN_VD
from test_fixtures_real import source_for
from test_market import BrokenVendor, correlated_pair

from cli import parse_override, run
from ingest.cache import NullCache
from market.models import Bar
from market.provider import LadderedProvider, StaticMarketProvider


def msft_market_provider():
    return LadderedProvider(BrokenVendor(), BrokenVendor(), cache=NullCache())


def synthetic_provider(ticker: str):
    stock, spy = correlated_pair(slope=1.1, seed=11)
    stock = [Bar(day=b.day, close=b.close * 2) for b in stock]
    return StaticMarketProvider({ticker: stock, "SPY": spy})


class TestRender:
    def test_msft_summary_contains_every_required_section(self):
        out = run("MSFT", source_for("MSFT"), msft_market_provider(),
                  valuation_date=GOLDEN_VD)
        for needle in ("VALUE PER SHARE", "WARNINGS", "INCOME STATEMENT",
                       "ASSUMPTIONS", "WACC BUILD-UP",
                       "UNLEVERED FREE CASH FLOW", "TERMINAL VALUE",
                       "EV → EQUITY (gordon)", "EV → EQUITY (exit_multiple)",
                       "SENSITIVITY", "Cross-check"):
            assert needle in out, needle
        assert "MICROSOFT" in out.upper()
        # projection checks echoed; historical validation status shown
        assert "P1=pass" in out and "P2=pass" in out
        assert "Historical validation:" in out

    def test_inherited_warnings_surface_share_count_derived(self):
        # GOOGL's derived share history must be visible in the summary,
        # not discoverable later (owner requirement)
        out = run("GOOGL", source_for("GOOGL"), synthetic_provider("GOOGL"),
                  valuation_date=date(2026, 8, 14))
        assert "[ingest:share_count_derived]" in out

    def test_by_nature_filer_renders_without_gross_profit(self):
        out = run("MCD", source_for("MCD"), synthetic_provider("MCD"),
                  valuation_date=date(2026, 8, 14))
        assert "by_nature" in out
        assert "Gross profit" not in out          # absent, never zero

    def test_overrides_are_applied_and_marked(self):
        out = run("MSFT", source_for("MSFT"), msft_market_provider(),
                  overrides={"terminal_growth": 0.02},
                  valuation_date=GOLDEN_VD)
        assert "*terminal_growth" in out and "2.00%" in out

    def test_json_dump_is_machine_readable_and_complete(self):
        out = run("MSFT", source_for("MSFT"), msft_market_provider(),
                  valuation_date=GOLDEN_VD, as_json=True)
        doc = json.loads(out)
        for key in ("assumptions", "projections", "ufcf", "wacc", "terminal",
                    "bridges", "sensitivity", "checks", "warnings", "history"):
            assert key in doc, key
        assert doc["ticker"] == "MSFT"
        assert doc["wacc"]["wacc"] == pytest.approx(0.0998, abs=0.001)


class TestParseOverride:
    def test_number_bool_and_errors(self):
        assert parse_override("terminal_growth=0.02") == ("terminal_growth", 0.02)
        assert parse_override("midyear=false") == ("midyear", False)
        with pytest.raises(SystemExit):
            parse_override("nonsense")
        with pytest.raises(SystemExit):
            parse_override("beta=abc")
