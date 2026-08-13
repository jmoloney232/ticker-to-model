"""Loader for schema.yaml — the canonical line items and tag chains (spec 02).

The YAML is the source of truth; this module gives it types and structural
validation. Derive expressions in the YAML are documentation — the executable
derivations live in mapping.DERIVERS, and test_schema asserts the two sets match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

SCHEMA_PATH = Path(__file__).parent / "schema.yaml"

Statement = str  # "income" | "balance" | "cashflow"
MISSING_RULES = {"zero_logged", "derive", "omit"}


@dataclass(frozen=True)
class SchemaItem:
    name: str
    label: str
    statement: Statement
    shape: str                    # duration | instant
    required: bool
    tags: tuple[str, ...] = ()    # namespaced-or-bare; bare means us-gaap
    unit: str = "USD"
    missing_rule: str | None = None
    derive: str | None = None     # documentation of the derive rule
    selection: str = "annual"     # annual | latest (cover-page shares)
    cross_check: str | None = None
    notes: str | None = None

    def namespaced_tags(self) -> list[tuple[str, str]]:
        out = []
        for t in self.tags:
            ns, _, name = t.partition(":")
            out.append((ns, name) if name else ("us-gaap", t))
        return out


@dataclass
class Schema:
    version: str
    items: dict[str, SchemaItem] = field(default_factory=dict)

    def by_statement(self, statement: Statement) -> list[SchemaItem]:
        return [i for i in self.items.values() if i.statement == statement]


def _validate(items: list[SchemaItem]) -> None:
    names = [i.name for i in items]
    if len(names) != len(set(names)):
        raise ValueError("schema.yaml: duplicate item names")
    for i in items:
        if not i.tags and not i.derive:
            raise ValueError(f"schema.yaml: {i.name} has neither tags nor derive")
        if i.required and not i.tags:
            raise ValueError(f"schema.yaml: {i.name} required but tagless")
        if not i.required and i.missing_rule not in MISSING_RULES:
            raise ValueError(f"schema.yaml: {i.name} optional without a valid missing_rule")
        expected = "instant" if i.statement == "balance" else "duration"
        if i.shape != expected:
            raise ValueError(f"schema.yaml: {i.name} shape {i.shape!r}, expected {expected!r}")


@lru_cache(maxsize=1)
def load_schema() -> Schema:
    raw = yaml.safe_load(SCHEMA_PATH.read_text())
    items = [
        SchemaItem(
            name=r["name"],
            label=r["label"],
            statement=r["statement"],
            shape=r["shape"],
            required=r["required"],
            tags=tuple(r.get("tags", ())),
            unit=r.get("unit", "USD"),
            missing_rule=r.get("missing_rule"),
            derive=r.get("derive"),
            selection=r.get("selection", "annual"),
            cross_check=r.get("cross_check"),
            notes=r.get("notes"),
        )
        for r in raw["items"]
    ]
    _validate(items)
    return Schema(version=raw["meta"]["version"], items={i.name: i for i in items})
