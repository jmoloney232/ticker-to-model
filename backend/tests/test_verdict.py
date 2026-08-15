"""The verdict sentence — server-generated, one variant per state
(owner spec, frontend redesign 2026-08-15). Templates are tested as pure
functions on serialized leg dicts, plus end-to-end against fixtures."""

import pytest
from test_api import client  # noqa: F401 — fixture

from app.serialize import refusal_verdict, short_name, verdict_text
from ingest.errors import (
    FinancialCompanyError,
    InsufficientCoverageError,
    KnownUnsupportedError,
)


def leg(v=281.07, vs=-0.436, mult=None):
    detail = {"multiple": mult} if mult else {}
    return {"available": True, "value_per_share": v, "vs_price": vs,
            "tv_detail": detail}


def unavailable(code="exit_multiple_unavailable"):
    return {"available": False, "reason": {"code": code, "message": "",
                                           "detail": {}}}


def solved(implied=0.0624):
    return {"terminal_growth": {"derived": 0.025, "implied": implied,
                                "status": "solved", "target_price": 498.37}}


def no_solution(status):
    return {"terminal_growth": {"derived": 0.025, "implied": None,
                                "status": status, "target_price": 498.37}}


class TestShortName:
    def test_all_caps_with_suffix(self):
        assert short_name("MICROSOFT CORP") == "Microsoft"
        assert short_name("EXXON MOBIL CORP") == "Exxon Mobil"
        assert short_name("BOEING CO") == "Boeing"

    def test_ampersand_and_fixups(self):
        assert short_name("DEERE & CO") == "Deere"
        assert short_name("JPMORGAN CHASE & CO") == "JPMorgan Chase"
        assert short_name("MCDONALDS CORP") == "McDonald's"

    def test_mixed_case_kept_as_filed(self):
        assert short_name("Kraft Heinz Co") == "Kraft Heinz"


class TestVerdictTemplates:
    def test_both_legs_below_market_with_solve(self):
        v = verdict_text("MICROSOFT CORP", 0.025, 498.37,
                         leg(), leg(606.34, 0.217, mult=18.9), solved())
        assert v["state"] == "ok"
        assert v["text"] == (
            "At 2.5% long-run growth, Microsoft is worth $281 a share — 44% "
            "below its $498 price. The market is pricing in 6.2% growth "
            "forever — on this model's other assumptions.")

    def test_above_market_mirrors(self):
        v = verdict_text("VERIZON COMMUNICATIONS INC", 0.025, 40.0,
                         leg(55.0, 0.375), leg(60.0, 0.5, mult=8.0), solved(0.011))
        assert "above its $40 price" in v["text"]
        assert "1.1% growth forever" in v["text"]

    def test_no_solution_below_wacc(self):
        v = verdict_text("KRAFT HEINZ CO", 0.025, 25.33,
                         leg(10.0, -0.6), leg(12.0, -0.5, mult=9.0),
                         no_solution("no_solution_below_wacc"))
        assert "No growth rate below the discount rate closes that gap" \
            in v["text"]

    def test_no_solution_in_range_reads_as_rich(self):
        v = verdict_text("VERIZON COMMUNICATIONS INC", 0.025, 40.0,
                         leg(70.0, 0.75), leg(75.0, 0.875, mult=8.0),
                         no_solution("no_solution_in_range"))
        assert "keeps it above the market" in v["text"]

    def test_negative_equity_reframes_not_a_price_target(self):
        v = verdict_text("KRAFT HEINZ CO", 0.025, 25.33,
                         leg(-12.96, -1.512), unavailable(), None)
        assert v["state"] == "negative_equity"
        assert "$-13" not in v["text"] and "-13" not in v["text"]
        assert "leaving nothing for shareholders" in v["text"]
        assert "no market multiple applies" in v["text"]   # exit clause rides

    def test_negative_anchor_uses_exit_leg(self):
        v = verdict_text("BOEING CO", 0.025, 180.0,
                         unavailable("terminal_anchor_negative"),
                         leg(150.0, -0.167, mult=12.0), None)
        assert v["state"] == "no_gordon"
        assert "isn't defined for Boeing" in v["text"]
        assert "12.0× exit multiple" in v["text"]
        assert "17% below the $180 price" in v["text"]

    def test_neither_leg(self):
        v = verdict_text("BOEING CO", 0.025, 180.0,
                         unavailable("terminal_anchor_negative"),
                         unavailable("terminal_anchor_negative"), None)
        assert v["state"] == "no_legs"
        assert "Neither method produces a defensible number" in v["text"]

    def test_small_price_prints_cents(self):
        v = verdict_text("PENNY CO", 0.02, 4.51, leg(3.10, -0.313),
                         leg(3.5, -0.22, mult=6.0), None)
        assert "$3.10" in v["text"] and "$4.51" in v["text"]


class TestRefusalVerdicts:
    def test_bank(self):
        exc = FinancialCompanyError("JPM", "JPMORGAN CHASE & CO", 6021,
                                    "commercial bank")
        text = refusal_verdict("financial_company", exc)
        assert "deposits and float are raw material" in text
        assert "by design" in text

    def test_coverage_names_the_financing_arm(self):
        exc = InsufficientCoverageError(
            "DE", 0.54, 0.61, 0.60,
            ["us-gaap:FinancingReceivable... $44.7B"])
        text = refusal_verdict("insufficient_coverage", exc)
        assert "only 54% of assets tie" in text
        assert "financing arm, a lending business" in text
        assert "declines" in text

    def test_unsupported_filer(self):
        exc = KnownUnsupportedError("XOM", "extension-tag filer")
        text = refusal_verdict("known_unsupported", exc)
        assert "company-specific tags" in text
        assert "known limitation, not a data error" in text


class TestVerdictEndToEnd:
    def test_msft_verdict_in_payload(self, client):  # noqa: F811
        body = client.post("/api/model/MSFT", json={}).json()
        v = body["verdict"]
        assert v["state"] == "ok"
        assert "Microsoft is worth $281 a share" in v["text"]
        assert "44% below its $498 price" in v["text"]
        assert "growth forever — on this model's other assumptions" \
            in v["text"]

    def test_khc_negative_equity_end_to_end(self, client):  # noqa: F811
        body = client.post("/api/model/KHC", json={}).json()
        v = body["verdict"]
        assert v["state"] == "negative_equity"
        assert "leaving nothing for shareholders" in v["text"]

    def test_jpm_refusal_verdict(self, client):  # noqa: F811
        body = client.post("/api/model/JPM", json={}).json()
        assert body["status"] == "unsupported"
        assert "enterprise DCF doesn't apply" in body["verdict"]


@pytest.mark.parametrize("filed,expected", [
    ("COSTCO WHOLESALE CORP /NEW", "Costco Wholesale"),
    ("COCA COLA CO", "Coca Cola"),
])
def test_short_name_edges(filed, expected):
    assert short_name(filed) == expected
