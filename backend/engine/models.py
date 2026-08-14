"""Data shapes for the valuation engine (specs/04-engine.md, Outputs).

Pure dataclasses. The engine imports typed dataclasses only (CLAUDE.md,
engine purity) — ingest and market models are exactly that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ingest.models import FinancialHistory, ValidationReport
from market.models import MarketInputs


@dataclass
class Assumption:
    """One editable input: derived default + derivation text + optional
    override. The UI/CLI/workbook show all three; the engine uses .effective."""

    name: str
    value: float | bool | str | None      # derived default (None = unavailable)
    derivation: str
    unit: str = "ratio"                   # ratio | rate | days | x | usd | shares | flag
    override: float | bool | None = None

    @property
    def effective(self):
        return self.override if self.override is not None else self.value


@dataclass
class Assumptions:
    fields: dict[str, Assumption]
    cost_structure: str                   # by_function | by_nature — branches ratios

    def eff(self, name: str):
        return self.fields[name].effective

    def has(self, name: str) -> bool:
        return name in self.fields


@dataclass
class ProjectedPeriod:
    fiscal_year: int
    fye: date
    income: dict[str, float] = field(default_factory=dict)
    balance: dict[str, float] = field(default_factory=dict)
    cashflow: dict[str, float] = field(default_factory=dict)


@dataclass
class UFCFYear:
    fiscal_year: int
    ebit: float
    tax_rate: float
    nopat: float
    d_and_a: float
    sbc_addback: float                    # 0 unless the toggle is on
    capex: float
    delta_nwc: float
    ufcf: float
    exponent: float                       # discount exponent in years
    discount_factor: float
    pv: float


@dataclass
class WaccBuild:
    """Every intermediate exposed — the build-up is an output, not a scalar."""

    risk_free: float
    erp: float
    beta_used: float
    beta_source: str                      # blume | raw | override | fallback_1.0
    cost_of_equity: float
    coverage: float | None                # None = no traceable interest expense
    rating: str
    spread: float
    kd_method: str                        # synthetic | embedded
    kd_pretax: float
    kd_after_tax: float
    marginal_tax: float
    market_cap: float
    gross_debt: float
    weight_equity: float
    weight_debt: float
    wacc: float


@dataclass
class TerminalLeg:
    method: str                           # gordon | exit_multiple
    value_at_fyeN: float
    exponent: float
    pv: float
    detail: dict[str, float] = field(default_factory=dict)


@dataclass
class BridgeItem:
    name: str
    value: float
    source: str = "tag"                   # tag | derived | zero_logged | computed
    note: str = ""


@dataclass
class Bridge:
    method: str
    enterprise_value: float
    items: list[BridgeItem]               # signed adjustments EV -> equity
    equity_value: float
    shares: float
    value_per_share: float


@dataclass
class SensitivityGrid:
    """5×5 value-per-share grid; None cells = invalid combination (g ≥ WACC)."""

    row_label: str                        # WACC
    col_label: str                        # terminal g | exit multiple
    rows: list[float]                     # WACC values
    cols: list[float]
    cells: list[list[float | None]]


@dataclass
class EngineWarning:
    code: str
    message: str
    detail: dict = field(default_factory=dict)


@dataclass
class ModelResult:
    ticker: str
    valuation_date: date
    history: FinancialHistory
    market: MarketInputs
    assumptions: Assumptions
    projections: list[ProjectedPeriod]
    ufcf: list[UFCFYear]
    wacc: WaccBuild
    terminal: dict[str, TerminalLeg]      # gordon (+ exit_multiple when available)
    crosschecks: dict[str, float]         # implied_exit_multiple / implied_terminal_g
    bridges: dict[str, Bridge]
    sensitivity: dict[str, SensitivityGrid]
    checks: ValidationReport              # projection invariants P1..P8
    warnings: list[EngineWarning]         # engine-born; inherited live on history/market

    def all_warnings(self) -> list[tuple[str, str, str]]:
        """(origin, code, message) across every stream — the CLI/UI contract:
        inherited ingest and market warnings must never be droppable."""
        out = [("ingest", w.code, w.message) for w in self.history.warnings]
        out += [("market", w.code, w.message) for w in self.market.warnings]
        out += [("engine", w.code, w.message) for w in self.warnings]
        return out
