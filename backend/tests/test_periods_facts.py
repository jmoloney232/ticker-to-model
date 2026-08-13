"""Fiscal-calendar and fact-selection policy tests (spec 01 §4)."""

from datetime import date

from ingest.facts import (
    RawFact,
    select_durations_by_fy,
    select_instant_at,
    select_latest,
)
from ingest.periods import fiscal_year_label, is_53_week, is_annual


class TestFiscalYearLabel:
    def test_calendar_year(self):
        assert fiscal_year_label(date(2023, 12, 31), "1231") == 2023

    def test_june_fye_msft_style(self):
        assert fiscal_year_label(date(2024, 6, 30), "0630") == 2024

    def test_retail_year_ending_early_january(self):
        # FY "2021" for a retailer whose 52/53-week year ends Sunday near Dec 31
        assert fiscal_year_label(date(2022, 1, 2), "1231") == 2021

    def test_late_august_costco_style(self):
        assert fiscal_year_label(date(2023, 9, 3), "0831") == 2023

    def test_missing_anchor_defaults_to_calendar(self):
        assert fiscal_year_label(date(2023, 12, 31), None) == 2023


class TestDurationWindows:
    def test_365_day_year_is_annual(self):
        assert is_annual(date(2023, 1, 1), date(2023, 12, 31))

    def test_quarter_is_not_annual(self):
        assert not is_annual(date(2023, 1, 1), date(2023, 3, 31))

    def test_53_week_year(self):
        start, end = date(2022, 8, 29), date(2023, 9, 3)   # Costco FY2023: 371 days
        assert is_annual(start, end)
        assert is_53_week(start, end)

    def test_52_week_year_not_53(self):
        assert not is_53_week(date(2021, 8, 30), date(2022, 8, 28))


def _f(val, end, filed, accn, start=None, form="10-K"):
    return RawFact(value=val, unit="USD", end=end, start=start,
                   accession=accn, filed=filed, form=form)


class TestRestatementResolution:
    def test_latest_filed_wins_and_flags_material_delta(self):
        facts = [
            _f(1000.0, date(2016, 12, 31), date(2017, 2, 20), "a1",
               start=date(2016, 1, 1)),
            _f(950.0, date(2016, 12, 31), date(2019, 6, 7), "a2",   # restated later
               start=date(2016, 1, 1)),
        ]
        sel = select_durations_by_fy(facts, "1231")[2016]
        assert sel.value == 950.0
        assert sel.first_filed_value == 1000.0
        assert sel.was_restated
        assert abs(sel.restatement_delta_pct - 0.05) < 1e-9

    def test_identical_comparative_refile_is_not_a_restatement(self):
        facts = [
            _f(1000.0, date(2022, 12, 31), date(2023, 2, 15), "a1",
               start=date(2022, 1, 1)),
            _f(1000.0, date(2022, 12, 31), date(2024, 2, 15), "a2",  # comparative year
               start=date(2022, 1, 1)),
        ]
        sel = select_durations_by_fy(facts, "1231")[2022]
        assert not sel.was_restated
        assert sel.restatement_delta_pct is None

    def test_sub_one_percent_delta_not_flagged(self):
        facts = [
            _f(1000.0, date(2022, 12, 31), date(2023, 2, 15), "a1",
               start=date(2022, 1, 1)),
            _f(1005.0, date(2022, 12, 31), date(2024, 2, 15), "a2",
               start=date(2022, 1, 1)),
        ]
        sel = select_durations_by_fy(facts, "1231")[2022]
        assert sel.value == 1005.0          # latest still wins
        assert not sel.was_restated         # but 0.5% is refiling noise


class TestInstantSelection:
    def test_exact_date_preferred(self):
        facts = [_f(10.0, date(2023, 12, 31), date(2024, 2, 15), "a1")]
        got = select_instant_at(facts, date(2023, 12, 31))
        assert got is not None and got[0].value == 10.0 and got[1] is False

    def test_fuzzy_within_seven_days(self):
        facts = [_f(10.0, date(2024, 1, 2), date(2024, 3, 15), "a1")]
        got = select_instant_at(facts, date(2023, 12, 31))
        assert got is not None and got[1] is True

    def test_beyond_seven_days_is_no_match(self):
        facts = [_f(10.0, date(2023, 9, 30), date(2023, 11, 15), "a1")]
        assert select_instant_at(facts, date(2023, 12, 31)) is None


def test_select_latest_takes_newest_cover_date_any_form():
    facts = [
        _f(100.0, date(2024, 12, 31), date(2025, 2, 15), "a1", form="10-K"),
        _f(99.0, date(2025, 4, 25), date(2025, 4, 30), "q1", form="10-Q"),
    ]
    sel = select_latest(facts)
    assert sel is not None and sel.value == 99.0
