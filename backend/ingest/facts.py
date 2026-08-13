"""Fact extraction and selection from a companyfacts payload (spec 01 §4).

Policies implemented here:
- forms 10-K / 10-K/A only (annual scope) — except `selection: latest` items
  (cover-page shares), which take the most recent fact across all forms;
- annual-duration and instant windows per periods.py;
- restatement resolution: latest (filed, accession) wins per (item, fiscal year),
  with the first-filed value retained and a >1% delta flagged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .periods import INSTANT_FUZZY_DAYS, fiscal_year_label, is_annual

ANNUAL_FORMS = {"10-K", "10-K/A"}
RESTATEMENT_WARN_THRESHOLD = 0.01


@dataclass(frozen=True)
class RawFact:
    value: float
    unit: str
    end: date
    start: date | None
    accession: str
    filed: date
    form: str


@dataclass(frozen=True)
class Selected:
    """One (item, fiscal year) resolution with restatement provenance."""

    value: float
    unit: str
    start: date | None
    end: date
    accession: str
    filed: date
    first_filed_value: float
    was_restated: bool
    restatement_delta_pct: float | None


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def iter_raw_facts(payload: dict, ns: str, tag: str, unit: str) -> list[RawFact]:
    node = payload.get("facts", {}).get(ns, {}).get(tag)
    if not node:
        return []
    out = []
    for fact in node.get("units", {}).get(unit, []):
        if fact.get("val") is None or not fact.get("end") or not fact.get("filed"):
            continue
        out.append(
            RawFact(
                value=float(fact["val"]),
                unit=unit,
                end=_parse_date(fact["end"]),
                start=_parse_date(fact["start"]) if fact.get("start") else None,
                accession=fact.get("accn", ""),
                filed=_parse_date(fact["filed"]),
                form=fact.get("form", ""),
            )
        )
    return out


def units_present(payload: dict, ns: str, tag: str) -> list[str]:
    node = payload.get("facts", {}).get(ns, {}).get(tag)
    return list(node.get("units", {}).keys()) if node else []


def annual_durations(payload: dict, ns: str, tag: str, unit: str) -> list[RawFact]:
    return [
        f for f in iter_raw_facts(payload, ns, tag, unit)
        if f.form in ANNUAL_FORMS and f.start is not None and is_annual(f.start, f.end)
    ]


def annual_instants(payload: dict, ns: str, tag: str, unit: str) -> list[RawFact]:
    return [
        f for f in iter_raw_facts(payload, ns, tag, unit)
        if f.form in ANNUAL_FORMS and f.start is None
    ]


def _resolve_group(group: list[RawFact]) -> Selected:
    """Latest (filed, accession) wins; first filed kept for the restatement delta."""
    first = min(group, key=lambda f: (f.filed, f.accession))
    chosen = max(group, key=lambda f: (f.filed, f.accession))
    delta = None
    restated = False
    if chosen.value != first.value:
        denom = max(abs(first.value), 1e-9)
        delta = abs(chosen.value - first.value) / denom
        restated = delta > RESTATEMENT_WARN_THRESHOLD
    return Selected(
        value=chosen.value,
        unit=chosen.unit,
        start=chosen.start,
        end=chosen.end,
        accession=chosen.accession,
        filed=chosen.filed,
        first_filed_value=first.value,
        was_restated=restated,
        restatement_delta_pct=delta,
    )


def select_durations_by_fy(facts: list[RawFact], anchor_mmdd: str | None) -> dict[int, Selected]:
    """Group annual durations by fiscal-year label and resolve restatements.

    Distinct end dates can map to one label only in pathological FYE changes;
    grouping by label (not end date) lets latest-filed win there too.
    """
    by_fy: dict[int, list[RawFact]] = {}
    for f in facts:
        by_fy.setdefault(fiscal_year_label(f.end, anchor_mmdd), []).append(f)
    return {fy: _resolve_group(group) for fy, group in by_fy.items()}


def select_instant_at(facts: list[RawFact], fye: date) -> tuple[Selected, bool] | None:
    """Instant matching a fiscal year end: exact date preferred, else nearest
    within +/-7 days (returned flag True => fuzzy match, caller warns)."""
    exact = [f for f in facts if f.end == fye]
    if exact:
        return _resolve_group(exact), False
    near = [f for f in facts if abs((f.end - fye).days) <= INSTANT_FUZZY_DAYS]
    if not near:
        return None
    best_date = min({f.end for f in near}, key=lambda d: abs((d - fye).days))
    return _resolve_group([f for f in near if f.end == best_date]), True


def select_latest(facts: list[RawFact]) -> Selected | None:
    """`selection: latest` items (cover-page shares): newest fact wins, any form."""
    if not facts:
        return None
    latest_end = max(f.end for f in facts)
    return _resolve_group([f for f in facts if f.end == latest_end])
