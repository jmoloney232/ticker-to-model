"""Fiscal-calendar policy (spec 01 §4, decision log).

- Annual duration: 340–380 days inclusive; >=371 days marks a 53-week year.
- Fiscal-year label: the calendar year whose anchor FYE date (MMDD from
  submissions) is nearest the period end. Handles June FYEs (MSFT FY2024 ends
  Jun 2024), retail years ending early January (FYE 2022-01-02 labels FY2021),
  and 52/53-week drift around the anchor.
"""

from __future__ import annotations

import calendar
from datetime import date

ANNUAL_MIN_DAYS = 340
ANNUAL_MAX_DAYS = 380
WEEK53_MIN_DAYS = 371
INSTANT_FUZZY_DAYS = 7


def duration_days(start: date, end: date) -> int:
    return (end - start).days + 1


def is_annual(start: date, end: date) -> bool:
    return ANNUAL_MIN_DAYS <= duration_days(start, end) <= ANNUAL_MAX_DAYS


def is_53_week(start: date, end: date) -> bool:
    return duration_days(start, end) >= WEEK53_MIN_DAYS


def _anchor_date(year: int, mmdd: str) -> date:
    month, day = int(mmdd[:2]), int(mmdd[2:])
    day = min(day, calendar.monthrange(year, month)[1])   # clamp e.g. 0229
    return date(year, month, day)


def fiscal_year_label(end: date, anchor_mmdd: str | None) -> int:
    """Calendar year whose anchor FYE is nearest `end` (candidates: y-1, y, y+1)."""
    if not anchor_mmdd or len(anchor_mmdd) != 4:
        anchor_mmdd = "1231"
    candidates = [end.year - 1, end.year, end.year + 1]
    return min(candidates, key=lambda y: abs((end - _anchor_date(y, anchor_mmdd)).days))
