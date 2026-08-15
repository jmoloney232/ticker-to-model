"""API contract tests (phase 4, part 1). Owner-mandated coverage:
every state — ok, warnings-on-valid, unavailable legs, refused, unsupported,
unknown — plus: rendered values match the engine's own output for the same
inputs; the downloaded workbook matches what's on screen; and assumption
edits never re-trigger upstream fetches."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_cli import msft_market_provider, synthetic_provider
from test_engine import GOLDEN_VD, msft_model
from test_excel import SOFFICE, recalc
from test_fixtures_real import source_for

from app import create_app
from ingest.errors import InsufficientCoverageError, UnknownTickerError

VD = GOLDEN_VD.isoformat()


class CountingSource:
    """Wraps a StaticSource and counts companyfacts fetches — the contract:
    assumption edits reuse the assembled history, never refetch."""

    def __init__(self, inner):
        self.inner = inner
        self.fetches = 0

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if name == "get_companyfacts":
            def counted(*a, **k):
                self.fetches += 1
                return attr(*a, **k)
            return counted
        return attr


class RaisingSource:
    def __init__(self, exc):
        self.exc = exc

    def resolve_cik(self, ticker):
        raise self.exc


COUNTERS: dict[str, CountingSource] = {}


def edgar_for(ticker: str):
    t = ticker.upper()
    if t == "NOPE":
        return RaisingSource(UnknownTickerError(t))
    if t == "DE":
        return RaisingSource(InsufficientCoverageError(
            "DE", 0.42, 0.51, 0.60,
            ["OtherAssets $18.4B", "OtherLiabilities $12.1B"]))
    if t == "XOM":
        return object()   # known-unsupported gate fires before any source call
    if t not in COUNTERS:
        COUNTERS[t] = CountingSource(source_for(t))
    return COUNTERS[t]


def provider_for(ticker: str):
    t = ticker.upper()
    if t in ("MSFT", "KHC", "JPM"):
        return msft_market_provider()      # snapshot fixtures exist
    return synthetic_provider(t)


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(edgar_for=edgar_for,
                                 provider_for=provider_for))


class TestModelOk:
    def test_values_match_the_engines_own_output(self, client):
        # owner test: what the API renders == what the engine computes
        r = client.post("/api/model/MSFT", json={"valuation_date": VD})
        assert r.status_code == 200
        doc = r.json()
        assert doc["status"] == "ok"
        m = msft_model()
        g = doc["valuation"]["gordon"]
        assert g["available"] is True
        assert g["value_per_share"] == pytest.approx(
            m.bridges["gordon"].value_per_share, rel=1e-12)
        assert g["vs_price"] == pytest.approx(
            m.bridges["gordon"].value_per_share / m.market.price.value - 1)
        assert g["tv_share_of_ev"] == pytest.approx(
            m.terminal["gordon"].pv / m.bridges["gordon"].enterprise_value)
        assert doc["valuation"]["exit_multiple"]["value_per_share"] == \
            pytest.approx(m.bridges["exit_multiple"].value_per_share, rel=1e-12)
        assert doc["wacc"]["wacc"] == pytest.approx(m.wacc.wacc, rel=1e-12)
        assert (doc["sensitivity"]["wacc_x_g"]["cells"][2][2]
                == pytest.approx(m.sensitivity["wacc_x_g"].cells[2][2]))
        assert (doc["projections"][-1]["income"]["operating_income"]
                == pytest.approx(m.projections[-1].income["operating_income"]))

    def test_assumption_rows_carry_the_full_contract(self, client):
        doc = client.post("/api/model/MSFT",
                          json={"valuation_date": VD}).json()
        rows = {a["name"]: a for a in doc["assumptions"]}
        tg = rows["terminal_growth"]
        # MSFT classifies compounder+reinvestment_heavy: profile-owned
        # defaults disclose the profile in their provenance (owner spec)
        assert tg["provenance"] == \
            "derived (profile: compounder+reinvestment_heavy)"
        assert tg["value"] == tg["derived_default"]
        assert tg["unit"] == "rate" and tg["rule"]
        assert rows["capex_pct"]["provenance"] == "derived"  # untouched field
        assert rows["revenue_cagr_uncapped"]["editable"] is False
        assert doc["provenance_counts"]["derived"] == len(doc["assumptions"])

    def test_warnings_are_structured_not_strings(self, client):
        # MCD: by-nature filer carrying unclassified_costs + lease warnings
        doc = client.post("/api/model/MCD", json={"valuation_date": VD}).json()
        assert doc["status"] == "ok"
        codes = {w["code"] for w in doc["warnings"]}
        assert "unclassified_costs" in codes
        w = next(w for w in doc["warnings"] if w["code"] == "unclassified_costs")
        assert w["origin"] == "engine"
        assert w["detail"]["unclassified_costs_pct"] > 0.30
        assert doc["company"]["cost_structure"] == "by_nature"

    def test_reverse_solves_included_for_the_comparison(self, client):
        doc = client.post("/api/model/MSFT", json={"valuation_date": VD}).json()
        tg = doc["reverse"]["terminal_growth"]
        assert tg["status"] == "solved"
        assert tg["implied"] > tg["derived"]
        # a no-solution is NAMED, never numbered (taxonomy contract; whether
        # MSFT's capex solve brackets depends on the profile-aware gap)
        capex = doc["reverse"]["capex_pct"]
        assert capex["status"] in ("solved", "no_solution_in_range")
        assert (capex["implied"] is None) == (capex["status"] != "solved")

    def test_share_count_derived_filer_passes_warning_through(self, client):
        doc = client.post("/api/model/GOOGL",
                          json={"valuation_date": VD}).json()
        assert doc["status"] == "ok"
        assert any(w["code"] == "share_count_derived" for w in doc["warnings"])


class TestLayering:
    def test_preset_then_override_provenance(self, client):
        doc = client.post("/api/model/MSFT", json={
            "valuation_date": VD, "preset": "street_convention",
            "overrides": {"terminal_growth": 0.03}}).json()
        rows = {a["name"]: a for a in doc["assumptions"]}
        assert rows["terminal_growth"]["provenance"] == "user"
        assert rows["terminal_growth"]["value"] == 0.03
        # profile-aware default: MSFT's compounder profile sets g at the 10Y
        assert rows["terminal_growth"]["derived_default"] == pytest.approx(
            doc["market"]["risk_free"]["value"])
        assert (rows["effective_tax_fy1"]["provenance"]
                == "preset:street_convention")
        assert rows["rnd_pct"]["provenance"] == "derived"
        assert doc["preset"]["rationale"]
        counts = doc["provenance_counts"]
        assert counts["user"] == 1 and counts["preset"] >= 2

    def test_boolean_override_survives_json(self, client):
        doc = client.post("/api/model/MSFT", json={
            "valuation_date": VD, "overrides": {"midyear": False}}).json()
        rows = {a["name"]: a for a in doc["assumptions"]}
        assert rows["midyear"]["value"] is False
        assert rows["midyear"]["provenance"] == "user"

    def test_code_reproduces_the_screen(self, client):
        # owner requirement: the compact code works in both directions and a
        # link reproduces the exact model
        r = client.post("/api/code", json={
            "preset": "street_convention",
            "overrides": {"terminal_growth": 0.03}})
        code = r.json()["code"]
        decoded = client.get(f"/api/code/{code}").json()
        assert decoded == {"preset": "street_convention",
                           "overrides": {"terminal_growth": 0.03}}
        explicit = client.post("/api/model/MSFT", json={
            "valuation_date": VD, "preset": "street_convention",
            "overrides": {"terminal_growth": 0.03}}).json()
        via_code = client.get(
            f"/api/model/MSFT?code={code}&valuation_date={VD}").json()
        assert (via_code["valuation"]["gordon"]["value_per_share"]
                == explicit["valuation"]["gordon"]["value_per_share"])
        assert via_code["code"] == explicit["code"]

    def test_edits_do_not_refetch_upstream(self, client):
        client.post("/api/model/MSFT", json={"valuation_date": VD})
        before = COUNTERS["MSFT"].fetches
        for g in (0.020, 0.021, 0.022):
            r = client.post("/api/model/MSFT", json={
                "valuation_date": VD, "overrides": {"terminal_growth": g}})
            assert r.status_code == 200
        assert COUNTERS["MSFT"].fetches == before   # owner: fetched once, reused


class TestStates:
    def test_bank_is_unsupported_not_an_error(self, client):
        r = client.post("/api/model/JPM", json={"valuation_date": VD})
        assert r.status_code == 200               # a feature, not a failure
        doc = r.json()
        assert doc["status"] == "unsupported"
        assert doc["reason"]["code"] == "financial_company"
        assert doc["reason"]["detail"]["sic"] == 6021
        assert "not supported" in doc["reason"]["message"]

    def test_known_unsupported_filer(self, client):
        doc = client.post("/api/model/XOM", json={"valuation_date": VD}).json()
        assert doc["status"] == "unsupported"
        assert doc["reason"]["code"] == "known_unsupported"

    def test_coverage_refusal_names_the_unattributed_balances(self, client):
        doc = client.post("/api/model/DE", json={"valuation_date": VD}).json()
        assert doc["status"] == "refused"
        assert doc["reason"]["code"] == "insufficient_coverage"
        assert doc["reason"]["detail"]["assets_named_share"] == 0.42
        assert "OtherAssets" in doc["reason"]["message"]

    def test_unknown_ticker_is_404(self, client):
        r = client.post("/api/model/NOPE", json={"valuation_date": VD})
        assert r.status_code == 404
        assert "not found" in r.json()["detail"]

    def test_unavailable_leg_is_data_with_reason(self, client):
        # KHC: FY0 EBITDA <= 0 -> exit leg honestly unavailable, gordon runs
        doc = client.post("/api/model/KHC", json={"valuation_date": VD}).json()
        assert doc["status"] == "ok"
        exit_leg = doc["valuation"]["exit_multiple"]
        assert exit_leg["available"] is False
        assert exit_leg["reason"]["code"] == "exit_multiple_unavailable"
        assert doc["valuation"]["gordon"]["available"] is True

    def test_invalid_override_is_400_with_the_constraint(self, client):
        r = client.post("/api/model/MSFT", json={
            "valuation_date": VD, "overrides": {"terminal_growth": 0.50}})
        assert r.status_code == 400
        assert "terminal g within" in r.json()["detail"]

    def test_garbage_code_is_400(self, client):
        r = client.get("/api/model/MSFT?code=!!notacode!!")
        assert r.status_code == 400

    def test_unknown_preset_is_400_and_lists_available(self, client):
        r = client.post("/api/model/MSFT", json={
            "valuation_date": VD, "preset": "yolo"})
        assert r.status_code == 400
        assert "street_convention" in r.json()["detail"]


class TestSurfaces:
    def test_presets_listing_carries_rationales_and_rules(self, client):
        doc = client.get("/api/presets").json()
        by_name = {p["name"]: p for p in doc["presets"]}
        assert set(by_name) >= {"derived", "market_implied",
                                "street_convention", "downside"}
        assert by_name["downside"]["rationale"]
        forms = {f["field"]: f["form"]
                 for f in by_name["street_convention"]["fields"]}
        assert forms["terminal_growth"] == "rule"

    def test_methodology_surface(self, client):
        doc = client.get("/api/methodology").json()
        ids = {c["id"] for c in doc["conventions"]}
        assert {"unclassified_costs", "assumption_presets",
                "negative_terminal_anchor"} <= ids
        assert len(doc["presets"]) >= 4


@pytest.mark.skipif(SOFFICE is None,
                    reason="LibreOffice not found — screen==workbook gate "
                           "did not run")
class TestScreenEqualsDownload:
    def test_workbook_matches_the_screen(self, client, tmp_path):
        """Owner test: same ticker, same preset, same overrides — the
        downloaded workbook recalculates to the numbers on screen."""
        code = client.post("/api/code", json={
            "preset": "street_convention",
            "overrides": {"capex_pct": 0.20}}).json()["code"]
        screen = client.get(
            f"/api/model/MSFT?code={code}&valuation_date={VD}").json()
        r = client.get(f"/api/workbook/MSFT.xlsx?code={code}"
                       f"&valuation_date={VD}")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith(
            "application/vnd.openxmlformats")
        path = tmp_path / "MSFT.xlsx"
        path.write_bytes(r.content)
        wb = recalc(path)
        cover = wb["Cover"]
        labels = {c.value: cover.cell(row=c.row, column=2).value
                  for c in cover["A"] if c.value}
        assert labels["Value per share — Gordon"] == pytest.approx(
            screen["valuation"]["gordon"]["value_per_share"], rel=1e-6)
        assert labels["Value per share — exit multiple"] == pytest.approx(
            screen["valuation"]["exit_multiple"]["value_per_share"], rel=1e-6)
        assert labels["Active preset"] == "Street convention"

    def test_workbook_refused_for_refused_filer(self, client):
        r = client.get(f"/api/workbook/JPM.xlsx?valuation_date={VD}")
        assert r.status_code == 409
        assert "not supported" in r.json()["detail"]


def test_audit_guide_served_as_markdown_data(client):
    """The committed audit guide reaches the Audit tab as data — the
    frontend renders a safe subset, no HTML passes through."""
    body = client.get("/api/audit-guide").json()
    assert "markdown" in body
    assert body["markdown"].startswith("# Financial Assumptions")
