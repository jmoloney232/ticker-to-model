"""The Summary tab's warnings digest: plain sentences, grouped, nothing
dropped — codes and counts survive so the Audit tab can expand every entry."""

from test_api import client  # noqa: F401 — fixture

from app.serialize import warnings_digest


def w(code, severity="warn", fiscal_year=2024, item=None,
      message="raw message"):
    return {"origin": "ingest", "code": code, "message": message,
            "fiscal_year": fiscal_year, "item": item, "severity": severity,
            "detail": {}}


class TestDigestGrouping:
    def test_unmapped_group_reads_as_one_sentence(self):
        ws = [w("unmapped_item", fiscal_year=2020 + i, item=f"item_{i}")
              for i in range(6)]
        d = warnings_digest(ws, "Microsoft")
        assert len(d) == 1
        entry = d[0]
        assert entry["count"] == 6
        assert entry["codes"] == ["unmapped_item"]
        assert "Microsoft doesn't report every minor line item separately" \
            in entry["text"]
        assert "6 optional lines across 6 fiscal years" in entry["text"]

    def test_single_unmapped_names_the_item(self):
        d = warnings_digest([w("unmapped_item", item="interest_income")],
                            "Coca Cola")
        assert "interest_income" in d[0]["text"]

    def test_coverage_low_is_hard_and_leads(self):
        ws = [w("unmapped_item") for _ in range(9)] + \
            [w("coverage_low", message="coverage 62% — hard warning")]
        d = warnings_digest(ws, "NVIDIA")
        assert d[0]["hard"] is True
        assert d[0]["codes"] == ["coverage_low"]
        assert d[0]["text"] == "coverage 62% — hard warning"

    def test_info_notes_sort_after_warnings(self):
        ws = [w("terminal_excess_return_persistent", severity="info",
                message="note text"),
              w("restated")]
        d = warnings_digest(ws, "X")
        assert d[0]["severity"] == "warn"
        assert d[-1]["severity"] == "info"

    def test_unknown_code_passes_message_through_with_count(self):
        ws = [w("someday_new_code", message="the engine explains itself"),
              w("someday_new_code", message="second one")]
        d = warnings_digest(ws, "X")
        assert d[0]["text"] == "the engine explains itself (+1 more like this)"

    def test_week53_names_the_years(self):
        d = warnings_digest([w("week53", fiscal_year=2023)], "Costco")
        assert "FY2023 ran 53 weeks" in d[0]["text"]


class TestDigestEndToEnd:
    def test_msft_digest_present_and_covers_all_warnings(self, client):  # noqa: F811
        body = client.post("/api/model/MSFT", json={}).json()
        digest = body["warnings_digest"]
        assert sum(e["count"] for e in digest) == len(body["warnings"])
        assert all(e["text"] for e in digest)

    def test_khc_digest_mentions_restatement_plainly(self, client):  # noqa: F811
        body = client.post("/api/model/KHC", json={}).json()
        texts = " ".join(e["text"] for e in body["warnings_digest"])
        assert "changed in later filings" in texts
        assert "53 weeks" in texts
