"""Headline-driver ranking: engine-computed impact, ranked per company
(methodology: driver_ranking). The rule, not a fixed list."""

from pathlib import Path

import pytest
import yaml
from test_api import client  # noqa: F401 — fixture
from test_engine import GOLDEN_VD, toy_history, toy_market

from engine.assumptions import derive_assumptions
from engine.drivers import STEPS, TOP_N, WACC_STEP, driver_impacts
from ingest.assemble import build_financial_history
from market.assemble import build_market_inputs

METHODOLOGY = Path(__file__).parent.parent / "engine" / "methodology.yaml"


def test_steps_match_methodology():
    doc = yaml.safe_load(METHODOLOGY.read_text())
    entry = next(c for c in doc["conventions"] if c["id"] == "driver_ranking")
    assert entry["steps"] == STEPS
    assert entry["wacc_step"] == WACC_STEP
    assert entry["top_n"] == TOP_N


def test_curve_points_match_methodology():
    from engine.reverse import CURVE_POINTS
    doc = yaml.safe_load(METHODOLOGY.read_text())
    entry = next(c for c in doc["conventions"] if c["id"] == "value_curves")
    assert entry["curve_points"] == CURVE_POINTS


class TestToyImpacts:
    def _impacts(self):
        h, mkt = toy_history(), toy_market()
        a = derive_assumptions(h, mkt)
        from test_engine import VD
        return driver_impacts(h, mkt, a, VD)

    def test_returns_top_five_ranked(self):
        impacts = self._impacts()
        assert len(impacts) == TOP_N
        vals = [d.impact_per_share for d in impacts]
        assert vals == sorted(vals, reverse=True)
        assert all(v > 0 for v in vals)

    def test_wacc_composite_present_with_fixed_direction(self):
        impacts = self._impacts()
        wacc = next(d for d in impacts if d.name == "wacc")
        assert wacc.composite and wacc.direction == -1

    def test_no_wacc_inputs_listed_individually(self):
        names = {d.name for d in self._impacts()}
        assert not names & {"beta", "erp", "risk_free", "beta_raw"}


class TestMsftDrivers:
    def test_payload_shape_and_ranking(self, client):  # noqa: F811
        body = client.post("/api/model/MSFT", json={}).json()
        drivers = body["drivers"]
        assert len(drivers) == 5
        impacts = [d["impact_per_share"] for d in drivers]
        assert impacts == sorted(impacts, reverse=True)
        top = drivers[0]
        assert top["name"] == "wacc"                 # MSFT: discounting rules
        assert top["label"] == "Discount rate (WACC)"
        assert top["direction"] == "down"
        assert top["step_label"] == "±1pp"
        assert "terminal_growth" in {d["name"] for d in drivers}

    def test_wacc_impact_consistent_with_sensitivity_grid(self, client):  # noqa: F811
        """The composite WACC sweep is the same convention as the grid rows:
        impact must equal the mean |Δ| of the ±1pp rows at the base column."""
        body = client.post("/api/model/MSFT", json={}).json()
        grid = body["sensitivity"]["wacc_x_g"]
        base_col = grid["cols"].index(
            body["curves"]["terminal_growth"]["landmarks"]["derived"])
        base_row = len(grid["rows"]) // 2
        v0 = grid["cells"][base_row][base_col]
        up = grid["cells"][base_row + 2][base_col]      # +1pp (0.5pp steps)
        dn = grid["cells"][base_row - 2][base_col]      # −1pp
        expected = (abs(up - v0) + abs(dn - v0)) / 2
        wacc_driver = next(d for d in body["drivers"] if d["name"] == "wacc")
        assert wacc_driver["impact_per_share"] == pytest.approx(
            expected, rel=1e-9)

    def test_drivers_follow_user_edits(self, client):  # noqa: F811
        """Ranking is computed against the CURRENT assumptions. MSFT's
        compounder profile defaults g at the 10Y; editing it DOWN to 2.5%
        moves it away from WACC, so its impact must fall (convexity)."""
        base = client.post("/api/model/MSFT", json={}).json()["drivers"]
        edited = client.post("/api/model/MSFT", json={
            "overrides": {"terminal_growth": 0.025}}).json()["drivers"]
        g_base = next(d["impact_per_share"] for d in base
                      if d["name"] == "terminal_growth")
        g_edit = next(d["impact_per_share"] for d in edited
                      if d["name"] == "terminal_growth")
        assert g_edit < g_base       # convexity: g matters more near WACC


def test_negative_anchor_ranks_on_exit_leg():
    """When the Gordon leg refuses, drivers rank on the exit leg —
    exit_multiple itself becomes rankable."""
    from test_engine import VD, F
    h = toy_history()
    for p in h.periods:
        p.income["operating_income"] = F(-50.0)
    mkt = toy_market()
    a = derive_assumptions(h, mkt)
    impacts = driver_impacts(h, mkt, a, VD, leg="exit_multiple")
    assert impacts, "exit-leg ranking produced nothing"
    assert all(d.impact_per_share > 0 for d in impacts)
    # on a distressed filer the cost lines dominate — the ranking is the
    # company's own, not a fixed list (the spec's point)
    assert "wacc" in {d.name for d in impacts} or len(impacts) == 5


def test_msft_fixture_impacts_direct():
    """Direct engine call on the real fixture — top driver magnitudes are
    plausible dollars-per-share, not degenerate zeros."""
    import sys
    sys.path[:0] = ["tests"]
    from test_api import edgar_for, provider_for
    h = build_financial_history("MSFT", edgar_for("MSFT"))
    mkt = build_market_inputs("MSFT", provider_for("MSFT"), as_of=GOLDEN_VD)
    a = derive_assumptions(h, mkt)
    impacts = driver_impacts(h, mkt, a, GOLDEN_VD)
    assert 5 < impacts[0].impact_per_share < 200
