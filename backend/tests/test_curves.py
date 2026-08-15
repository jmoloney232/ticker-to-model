"""The slider's value curve: engine-computed points the frontend reads and
never interpolates (owner spec, redesign 2026-08-15). The gate that matters:
every curve point must equal a full build_model at that assumption value."""

import pytest
from test_api import client  # noqa: F401 — fixture
from test_engine import VD, F, toy_history, toy_market

from app.serialize import serialize_model
from engine.dcf import build_model


def curve_of(client, ticker="MSFT", body=None):  # noqa: F811
    payload = client.post(f"/api/model/{ticker}", json=body or {}).json()
    assert payload["status"] == "ok"
    return payload, payload["curves"]["terminal_growth"]


class TestCurveShape:
    def test_domain_points_and_order(self, client):  # noqa: F811
        payload, curve = curve_of(client)
        lo, hi = curve["domain"]
        xs = [p[0] for p in curve["points"]]
        assert lo == -0.02
        assert hi == pytest.approx(payload["wacc"]["wacc"] - 0.0025)
        assert len(xs) >= 25
        assert xs == sorted(xs)
        assert all(lo <= x <= hi for x in xs)

    def test_landmarks_are_exact_curve_points(self, client):  # noqa: F811
        payload, curve = curve_of(client)
        xs = {p[0] for p in curve["points"]}
        lm = curve["landmarks"]
        assert lm["derived"] in xs and lm["current"] in xs
        assert lm["market_implied"] in xs
        assert lm["rf"] in xs
        assert lm["market_implied"] == pytest.approx(
            payload["reverse"]["terminal_growth"]["implied"])
        assert lm["rf"] == payload["market"]["risk_free"]["value"]
        assert lm["block"] == curve["domain"][1]

    def test_thumb_at_rest_shows_the_hero_number(self, client):  # noqa: F811
        """The point at the current terminal growth equals the displayed
        Gordon value exactly — slider-at-rest never disagrees with the hero."""
        payload, curve = curve_of(client)
        current = curve["landmarks"]["current"]
        v = {x: v for x, v in curve["points"]}[current]
        assert v == pytest.approx(
            payload["valuation"]["gordon"]["value_per_share"], rel=1e-12)


class TestCurveParity:
    def test_points_match_full_recompute(self, client):  # noqa: F811
        """Five sampled points, endpoints included: the release recompute
        (a real override request) must agree with the curve the drag read."""
        _, curve = curve_of(client)
        pts = curve["points"]
        for x, v in [pts[0], pts[6], pts[12], pts[18], pts[-1]]:
            body = client.post("/api/model/MSFT",
                               json={"overrides": {"terminal_growth": x}}).json()
            assert body["status"] == "ok"
            assert body["valuation"]["gordon"]["value_per_share"] == \
                pytest.approx(v, rel=1e-9), f"curve diverges at g={x}"

    def test_other_edits_reshape_the_curve(self, client):  # noqa: F811
        _, base = curve_of(client)
        _, edited = curve_of(client, body={"overrides": {"capex_pct": 0.40}})
        base_at = {x: v for x, v in base["points"]}
        moved = [x for x, v in edited["points"]
                 if x in base_at and v != base_at[x]]
        assert moved, "capex override did not reshape the curve"


class TestCurveUnavailable:
    def test_negative_anchor_has_no_curve(self):
        # EBIT −5% of revenue: gordon leg refuses (negative terminal anchor)
        h = toy_history()
        for p in h.periods:
            p.income["operating_income"] = F(-50.0)
        m = build_model(h, toy_market(), valuation_date=VD)
        assert "gordon" not in m.bridges
        payload = serialize_model(m, None, None, None)
        assert payload["curves"] == {}
        assert payload["verdict"]["state"] in ("no_gordon", "no_legs")

    def test_khc_negative_values_still_curve(self, client):  # noqa: F811
        """Negative equity is a value, not an unavailability — the slider
        renders and shows the shortfall moving."""
        _, curve = curve_of(client, "KHC")
        vals = [v for _, v in curve["points"]]
        assert all(v is not None for v in vals)
