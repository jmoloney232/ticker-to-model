"""Company profile classifier (owner-approved 2026-08-15).

Reduces the model's structural bias: the diagnostic showed the gap to market
correlating −0.68 with WACC, −0.61 with beta, −0.41 with FY1 growth — the
error was predictable from company characteristics because every filer got
the same derived defaults. Profiles make the DERIVED layer company-aware;
the layering stays derived (profile-aware) → preset → user overrides.

Pure rules over measured inputs. assumptions.py computes the measurements
(with its own ROIC/margin/window definitions) and calls classify();
this module holds only thresholds, dataclasses, and the decision — so the
thresholds are parity-testable against methodology.yaml and nothing here
imports the rest of the engine.

Every threshold is set from what the category MEANS, never from fit against
market prices (owner rule). A profile may set: forecast horizon, growth fade
shape, terminal growth default, margin normalization, capex normalization.
It must not touch WACC components, the bridge, or validation thresholds —
enforced structurally (WACC is built before classification) and by test.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── thresholds (methodology: company_profiles; parity-tested) ────────────────
INFLATION_PROXY = 0.02      # declining: nominal growth below ≈ inflation is
#                             real shrinkage (both CAGR and latest year)
COMPOUNDER_CAGR_MIN = 0.08  # 2× the nominal-GDP proxy: growth economic-share
#                             retention cannot explain
COMPOUNDER_LATEST_MIN = 0.04  # still growing ≥ trend — kills rebound base
#                               effects (a recovering cyclical is not a
#                               compounder)
EXCESS_RETURN_MIN = 0.05    # median ROIC − WACC: below 5pp, invested-capital
#                             measurement noise can flip the sign
MARGIN_RANGE_MIN = 0.06     # cyclical: stable large-caps sit ≤5pp, true
#                             cyclicals ≥7pp; margin volatility WITHOUT a
#                             revenue down-year is a cost story, not a cycle
CAPEX_DA_MIN = 1.5          # reinvestment-heavy: same 1.5× already published
#                             as reinvestment_fade_mismatch's trigger.
#                             DEPRECIATION basis since 2026-08-17 (owner-
#                             approved): capex vs the assets capex replaces
CAPEX_DA_CAP = 4.0          # above this the ratio needs explaining — either
#                             the depreciation base is suspect or the spend is
#                             genuinely extreme; DEP_PPE_FLOOR separates them
DEP_PPE_FLOOR = 0.04        # suspect-base test (owner-approved 2026-08-17):
#                             min per-pair depreciation / beginning net PP&E
#                             below 4% claims a 25-year-plus asset life — a
#                             filer cannot BOTH depreciate that slowly AND be
#                             spending ≥ 4× depreciation on the same assets;
#                             the depreciation line isn't describing what the
#                             capex buys (MCD's lessee-D&A, CSCO's collapsed
#                             D&A-minus-amortization). A healthy rate with a
#                             ≥ 4× ratio is a real surge (ORCL's cloud
#                             buildout at ~18% ≈ 6-year server life) — the
#                             modifier applies. Universe gap is wide: suspect
#                             crossers measure ≤ 1.8%, genuine ≥ 8.8%

PRIMARIES = ("compounder", "mature", "declining")
MODIFIERS = ("cyclical", "reinvestment_heavy")


@dataclass(frozen=True)
class ProfileMeasures:
    """The classification inputs, measured from the filer's own history by
    the derive layer's definitions. All disclosed alongside the profile."""

    cagr: float                   # full-window revenue CAGR
    g_latest: float               # most recent year-over-year growth
    roic_median: float | None     # median full-window ROIC (None: IC ≤ 0)
    roic_years_above_wacc: int    # durability count
    roic_years: int               # observations
    wacc: float
    margin_range: float           # max − min EBIT margin over the window
    rev_down_years: int           # year-over-year revenue declines observed
    capex_da: float | None        # mean capex / depreciation, trailing 3y
    #                               (D&A − intangible amortization; wire name
    #                               kept for share-code/type stability)
    window: int                   # periods observed
    dep_ppe_min: float | None = None  # min per-pair depreciation / beginning
    #                               net PP&E over the same trailing pairs —
    #                               the suspect-base test's input; None when
    #                               no pair is measurable (treated as suspect)


@dataclass(frozen=True)
class Profile:
    primary: str                          # compounder | mature | declining
    modifiers: tuple[str, ...]            # subset of MODIFIERS
    measures: ProfileMeasures
    reassigned: bool = False              # user overrode the classification
    notes: tuple[str, ...] = field(default=())   # measurement caveats
    auto_tag: str = ""                    # what auto classification produced —
    #                                       kept through a reassignment so the
    #                                       provenance shows both (Part 3,
    #                                       owner spec 2026-08-17)

    @property
    def tag(self) -> str:
        mods = "".join(f"+{m}" for m in self.modifiers)
        return f"{self.primary}{mods}"


def classify(m: ProfileMeasures) -> Profile:
    """Primary from growth trajectory and excess returns; modifiers layer on.
    Mature is deliberately the fallback: a boundary case gets today's
    defaults, the conservative failure mode."""
    notes: list[str] = []

    if m.cagr < INFLATION_PROXY and m.g_latest < INFLATION_PROXY:
        primary = "declining"
    else:
        durable = (m.roic_years > 0
                   and m.roic_years_above_wacc == m.roic_years)
        spread = (m.roic_median - m.wacc
                  if m.roic_median is not None else None)
        if (m.cagr >= COMPOUNDER_CAGR_MIN
                and m.g_latest >= COMPOUNDER_LATEST_MIN
                and spread is not None and spread >= EXCESS_RETURN_MIN
                and durable):
            primary = "compounder"
        else:
            primary = "mature"

    modifiers: list[str] = []
    if m.margin_range >= MARGIN_RANGE_MIN:
        if m.rev_down_years >= 1:
            modifiers.append("cyclical")
        else:
            notes.append(
                f"margin range {m.margin_range:.1%} without a revenue "
                "down-year — treated as a cost story, not a cycle")
    if m.capex_da is not None:
        if CAPEX_DA_MIN <= m.capex_da < CAPEX_DA_CAP:
            modifiers.append("reinvestment_heavy")
        elif m.capex_da >= CAPEX_DA_CAP:
            # Separated conditions (owner-approved 2026-08-17): only a
            # suspect BASE withholds the modifier. A suspect base is
            # depreciation anomalous relative to the asset base; a real
            # surge is spending anomalous relative to the company's own
            # history and earns the modifier like any other crosser.
            if m.dep_ppe_min is None or m.dep_ppe_min < DEP_PPE_FLOOR:
                life = (f" ({1 / m.dep_ppe_min:.0f}-year implied asset life)"
                        if m.dep_ppe_min else "")
                notes.append(
                    f"capex/depreciation {m.capex_da:.2f}× exceeds the "
                    f"{CAPEX_DA_CAP:.0f}× cap with a suspect depreciation "
                    f"base — dep/PP&E "
                    + (f"{m.dep_ppe_min:.1%}{life}" if m.dep_ppe_min
                       is not None else "unmeasurable")
                    + " cannot describe the assets the capex buys; "
                    "reinvestment-heavy withheld")
            else:
                modifiers.append("reinvestment_heavy")
                notes.append(
                    f"capex/depreciation {m.capex_da:.2f}× exceeds the "
                    f"{CAPEX_DA_CAP:.0f}× cap with a sound depreciation "
                    f"base (dep/PP&E {m.dep_ppe_min:.1%} ≈ "
                    f"{1 / m.dep_ppe_min:.0f}-year life) — a genuine "
                    "reinvestment surge, not a measurement artifact; "
                    "modifier applied")

    auto = f"{primary}" + "".join(f"+{x}" for x in modifiers)
    return Profile(primary=primary, modifiers=tuple(modifiers),
                   measures=m, notes=tuple(notes), auto_tag=auto)


def parse_profile(tag: str) -> tuple[str, tuple[str, ...]]:
    """'compounder+cyclical' → ('compounder', ('cyclical',)) — the user
    reassignment format. Raises ValueError on unknown parts."""
    parts = tag.split("+")
    primary, mods = parts[0], tuple(parts[1:])
    if primary not in PRIMARIES:
        raise ValueError(f"unknown profile {primary!r} "
                         f"(one of: {', '.join(PRIMARIES)})")
    for mod in mods:
        if mod not in MODIFIERS:
            raise ValueError(f"unknown profile modifier {mod!r} "
                             f"(one of: {', '.join(MODIFIERS)})")
    if len(set(mods)) != len(mods):
        raise ValueError("duplicate profile modifier")
    return primary, mods
