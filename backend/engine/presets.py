"""Assumption presets (owner feature, 2026-08-14): a preset is a STATED
METHODOLOGY — name + one-line rationale + a derivation rule per field, held to
the same standard as the derived defaults. No hand-picked numbers: a preset
value that can't be traced to a rule doesn't ship.

Layering is strict and ordered: derive from history -> apply_preset ->
apply_overrides. Presets transform derived defaults; they never replace the
derivation. Every field carries provenance (derived | preset:<name> | user).

Presets live in engine/presets.yaml (data, not code — adding one requires no
code change; the methodology surface renders the file automatically). Two
field forms are supported from the start: `rule` (an expression evaluated
against the derived-value namespace) and `literal` (a fixed value, for
user-created presets later), plus `solved` (bisection via the reverse DCF).

Everything here is pure except load_presets() — the single config-file read,
the same boundary as ingest's schema.yaml; parse_presets() takes an already-
parsed dict so the app layer can load once and inject.

Rules are evaluated by a whitelisted-AST evaluator: names from the namespace,
numeric literals, + - * /, unary minus, and min/max/abs. No attribute access,
no other calls, no eval().
"""

from __future__ import annotations

import ast
import base64
import json
import operator
import zlib
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date
from pathlib import Path

import yaml

from ingest.models import FinancialHistory
from market.models import MarketInputs

from .assumptions import _identity_gap, _pct_of_revenue, _window, check_domain
from .errors import InvalidAssumptionError, PresetUnavailableError
from .models import Assumptions

PRESETS_PATH = Path(__file__).parent / "presets.yaml"
BUILTIN_NAMES = ("derived", "market_implied", "street_convention", "downside",
                 "damodaran_implied")
# Nominal-GDP proxy for the damodaran_implied preset's terminal-growth rule
# (≈ 2% real + 2% inflation). Parity-tested against methodology.yaml's
# nominal_gdp_proxy entry.
NOMINAL_GDP_PROXY = 0.04
_FORMS = ("rule", "literal", "solved")
_APPLICABILITY_KEYS = ("cost_structure", "min_history_years", "absent_warnings")


@dataclass(frozen=True)
class PresetField:
    name: str
    form: str                             # rule | literal | solved
    rule: str | None = None
    value: float | bool | None = None
    solver: str | None = None
    target: str | None = None
    optional: bool = False                # skip when structurally absent
    note: str | None = None               # provenance note (source, as-of) —
                                          # shown instead of the default note


@dataclass(frozen=True)
class Preset:
    name: str
    title: str
    rationale: str                        # required for built-ins
    builtin: bool
    applicability: dict
    fields: dict[str, PresetField] = dc_field(default_factory=dict)


def parse_presets(doc: dict) -> dict[str, Preset]:
    """Validate and parse an already-loaded presets.yaml document. Pure."""
    if doc.get("schema_version") != 1:
        raise ValueError("presets.yaml: unsupported schema_version "
                         f"{doc.get('schema_version')!r}")
    out: dict[str, Preset] = {}
    for entry in doc.get("presets", []):
        name = entry.get("name")
        if not name or name in out:
            raise ValueError(f"presets.yaml: missing or duplicate name {name!r}")
        builtin = bool(entry.get("builtin", False))
        rationale = (entry.get("rationale") or "").strip()
        if builtin and not rationale:
            raise ValueError(f"presets.yaml: built-in {name!r} has no rationale "
                             "— a published methodology needs a defence")
        applicability = entry.get("applicability") or {}
        for key in applicability:
            if key not in _APPLICABILITY_KEYS:
                raise ValueError(f"presets.yaml: {name!r} unknown applicability "
                                 f"key {key!r}")
        fields: dict[str, PresetField] = {}
        for fname, spec in (entry.get("fields") or {}).items():
            form = spec.get("form")
            if form not in _FORMS:
                raise ValueError(f"presets.yaml: {name}.{fname} form {form!r} "
                                 f"not in {_FORMS}")
            if form == "rule" and not spec.get("rule"):
                raise ValueError(f"presets.yaml: {name}.{fname} rule form "
                                 "without a rule expression")
            if form == "literal" and "value" not in spec:
                raise ValueError(f"presets.yaml: {name}.{fname} literal form "
                                 "without a value")
            if form == "solved" and (spec.get("solver") != "reverse_dcf"
                                     or spec.get("target") != "market_price"):
                raise ValueError(f"presets.yaml: {name}.{fname} solved form "
                                 "supports solver: reverse_dcf, "
                                 "target: market_price only")
            fields[fname] = PresetField(
                name=fname, form=form, rule=spec.get("rule"),
                value=spec.get("value"), solver=spec.get("solver"),
                target=spec.get("target"),
                optional=bool(spec.get("optional", False)),
                note=spec.get("note"))
        out[name] = Preset(name=name, title=entry.get("title", name),
                           rationale=rationale, builtin=builtin,
                           applicability=applicability, fields=fields)
    return out


def load_presets(path: Path = PRESETS_PATH) -> dict[str, Preset]:
    """The one file read in this module (config boundary, like schema.yaml)."""
    with open(path) as fh:
        return parse_presets(yaml.safe_load(fh))


# ── rule evaluation (whitelisted AST — no eval, no attributes) ───────────────

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv}
_FUNCS = {"min": min, "max": max, "abs": abs}


def evaluate_rule(expr: str, names: dict[str, float]) -> float:
    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if (isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id in names:
                return names[node.id]
            raise ValueError(f"unknown name {node.id!r}")
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _FUNCS and not node.keywords):
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ValueError(f"disallowed syntax {type(node).__name__}")

    try:
        return ev(ast.parse(expr, mode="eval"))
    except (SyntaxError, TypeError) as exc:
        raise ValueError(str(exc)) from exc


def rule_namespace(history: FinancialHistory, market: MarketInputs,
                   assumptions: Assumptions) -> dict[str, float]:
    """Names a rule may reference: every numeric derived default by its own
    field name, plus documented extras (spec 04, preset section)."""
    ns: dict[str, float] = {}
    for name, a in assumptions.fields.items():
        if (a.value is not None and isinstance(a.value, (int, float))
                and not isinstance(a.value, bool)):
            ns[name] = float(a.value)
    ns["market_price"] = market.price.value
    ns["nominal_gdp_proxy"] = NOMINAL_GDP_PROXY
    # beta_raw falls back to the derived beta (the 1.0 fallback) when the
    # regression is unavailable — max(beta_raw, 1.0) then resolves to 1.0,
    # which is the same stress the rule intends (disclosed in spec 04)
    ns.setdefault("beta_raw", ns.get("beta", 1.0))
    ns["da_pct_revenue_hist"] = _pct_of_revenue(_window(history), "d_and_a")

    worst = min(history.periods,
                key=lambda p: p.value("operating_income") / p.value("revenue"))
    rev = worst.value("revenue")
    ns["worst_fy"] = float(worst.fiscal_year)
    ns["worst_ebit_margin"] = worst.value("operating_income") / rev
    for item, key in (("cost_of_revenue", "worst_cogs_pct"),
                      ("research_and_development", "worst_rnd_pct"),
                      ("selling_general_admin", "worst_sga_pct"),
                      ("other_operating", "worst_other_opex_pct")):
        ns[key] = worst.value(item, 0.0) / rev
    ns["worst_unclassified_costs_pct"] = _identity_gap(worst)
    return ns


# ── application ──────────────────────────────────────────────────────────────

def applicability_reason(preset: Preset, history: FinancialHistory) -> str | None:
    """None = applicable; otherwise the stated reason it is not."""
    cond = preset.applicability
    allowed = cond.get("cost_structure")
    if allowed and history.cost_structure not in allowed:
        return (f"requires cost_structure in {list(allowed)}; "
                f"this filer is {history.cost_structure}")
    n = cond.get("min_history_years")
    if n and len(history.periods) < n:
        return (f"requires at least {n} years of history; "
                f"this filer has {len(history.periods)}")
    absent = cond.get("absent_warnings")
    if absent:
        present = sorted({w.code for w in history.warnings} & set(absent))
        if present:
            return "not valid with warnings present: " + ", ".join(present)
    return None


def apply_preset(assumptions: Assumptions, preset: Preset,
                 history: FinancialHistory, market: MarketInputs,
                 valuation_date: date) -> Assumptions:
    """Mark preset values onto the derived assumptions (layer 2 of 3). Preset
    values pass the same domain validation as user overrides; model-level
    validation (g < WACC etc.) still runs in build_model — presets never
    bypass it, and never suppress inherited warnings."""
    reason = applicability_reason(preset, history)
    if reason is not None:
        raise PresetUnavailableError(preset.name, reason)
    ns = rule_namespace(history, market, assumptions)

    for fname, spec in preset.fields.items():
        if fname not in assumptions.fields:
            if spec.optional:
                continue                  # structural absence, declared as such
            raise PresetUnavailableError(
                preset.name, f"field {fname!r} does not exist for this filer "
                             "(and is not marked optional)")
        if spec.form == "literal":
            value = spec.value
            note = spec.note or "literal (no derivation — user-form)"
        elif spec.form == "rule":
            try:
                value = evaluate_rule(spec.rule, ns)
            except ValueError as exc:
                raise PresetUnavailableError(
                    preset.name, f"rule for {fname!r} failed: {exc}") from exc
            note = f"rule: {spec.rule}"
        else:                             # solved — never a silent fallback
            from .reverse import implied_assumption  # lazy: reverse imports dcf
            r = implied_assumption(history, market, fname, valuation_date)
            if r.status != "solved":
                raise PresetUnavailableError(
                    preset.name,
                    f"no {fname} solution — {r.status.replace('_', ' ')} "
                    f"(target price {r.target_price:,.2f})")
            value = r.implied
            note = f"solved: Gordon leg = market price {r.target_price:,.2f}"
        try:
            check_domain(fname, value)     # same validation as user overrides
        except InvalidAssumptionError as exc:
            raise InvalidAssumptionError(
                fname, f"{exc.detail['constraint']} (requested by preset "
                       f"{preset.name!r})", value) from exc
        f = assumptions.fields[fname]
        f.preset_value = value
        f.preset_name = preset.name
        f.preset_note = note

    assumptions.active_preset = preset.name
    return assumptions


# ── compact encoding (shareable assumption sets — no persistence layer) ──────

def encode_assumption_set(preset: str | None,
                          overrides: dict[str, float | bool] | None,
                          profile: str | None = None) -> str:
    """Compact url-safe encoding of (preset name, user overrides, profile
    reassignment). Derived values are NOT encoded — they recompute from the
    filer's history, so a code stays honest as filings update. profile is
    encoded only when the user reassigned it (auto never encodes, so codes
    follow reclassifications as new filings land)."""
    payload: dict = {"v": 1}
    if preset:
        payload["p"] = preset
    if overrides:
        payload["o"] = overrides
    if profile:
        payload["pf"] = profile
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode().rstrip("=")


def decode_assumption_set(
        code: str) -> tuple[str | None, dict[str, float | bool], str | None]:
    try:
        raw = zlib.decompress(
            base64.urlsafe_b64decode(code + "=" * (-len(code) % 4)))
        doc = json.loads(raw)
    except Exception as exc:
        raise ValueError(f"invalid assumption-set code: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("v") != 1:
        raise ValueError("invalid assumption-set code: unsupported version")
    preset = doc.get("p")
    overrides = doc.get("o", {})
    if preset is not None and not isinstance(preset, str):
        raise ValueError("invalid assumption-set code: preset must be a name")
    if not isinstance(overrides, dict) or not all(
            isinstance(k, str) and isinstance(v, (int, float, bool))
            for k, v in overrides.items()):
        raise ValueError("invalid assumption-set code: overrides must map "
                         "field names to numbers or booleans")
    profile = doc.get("pf")
    if profile is not None:
        if not isinstance(profile, str):
            raise ValueError("invalid assumption-set code: profile must be "
                             "a tag")
        from .profile import parse_profile
        parse_profile(profile)            # raises ValueError on unknown tags
    return preset, overrides, profile
