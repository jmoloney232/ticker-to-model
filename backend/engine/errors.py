"""Typed errors for the valuation engine (specs/04-engine.md, Error cases)."""

from __future__ import annotations


class EngineError(Exception):
    def __init__(self, user_message: str, detail: dict | None = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail or {}


class InvalidAssumptionError(EngineError):
    """An override (or a derived input) violates a hard constraint. Always
    names the field and the constraint — 'invalid input' is not a message."""

    def __init__(self, field: str, constraint: str, value):
        super().__init__(
            f"Assumption {field!r} = {value!r} violates: {constraint}.",
            {"field": field, "constraint": constraint, "value": value},
        )
