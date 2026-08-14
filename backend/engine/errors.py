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


class PresetUnavailableError(EngineError):
    """A preset that cannot be applied to this filer SAYS SO — it never
    degrades quietly (owner guardrail). Always names the preset and why."""

    def __init__(self, preset: str, reason: str):
        super().__init__(
            f"Preset {preset!r} is unavailable for this filer: {reason}",
            {"preset": preset, "reason": reason},
        )
