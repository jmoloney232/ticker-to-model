"""In-house beta: 2y weekly OLS vs the benchmark (specs/03-market-data.md).

Owner-reviewed convention (re-confirmed 2026-08-13): 2 years of weekly returns —
Bloomberg's terminal default, including the Blume ⅔/⅓ adjustment. Weekly
sampling avoids the non-synchronous-trading bias that pulls daily OLS betas
toward zero (Scholes–Williams 1977, Dimson 1979).

Pure functions: bars in, BetaResult out. No I/O, no clock — the caller supplies
the window.
"""

from __future__ import annotations

from datetime import date

from .errors import InsufficientPriceHistoryError
from .models import Bar, BetaResult

MIN_OBSERVATIONS = 80      # target ~104 (2y of weeks); below 80 → recent IPO, refuse
BLUME_WEIGHT = 2.0 / 3.0   # β_adj = ⅔·β_raw + ⅓


def _weekly_closes(bars: list[Bar], common: set[date]) -> list[tuple[date, float]]:
    """Last close per ISO week, sampled only on dates both series share —
    the intersection first, so both series are sampled on identical dates."""
    by_week: dict[tuple[int, int], tuple[date, float]] = {}
    for bar in bars:
        if bar.day not in common:
            continue
        iso = bar.day.isocalendar()
        key = (iso.year, iso.week)
        prev = by_week.get(key)
        if prev is None or bar.day > prev[0]:
            by_week[key] = (bar.day, bar.close)
    return [by_week[k] for k in sorted(by_week)]


def _paired_weekly_returns(stock: list[Bar],
                           benchmark: list[Bar]) -> tuple[list[float], list[float]]:
    """Simple returns between CONSECUTIVE weeks only, paired index-for-index.
    Sampling dates are identical by construction (intersection first), and a
    missing week drops the pair on both sides — no silent forward-fill: a gap
    wider than 10 days is not a weekly return and would smear two weeks of
    movement into one observation."""
    common = {b.day for b in stock} & {b.day for b in benchmark}
    s = _weekly_closes(stock, common)
    m = _weekly_closes(benchmark, common)
    rs, rm = [], []
    for (sd0, sc0), (sd1, sc1), (_, mc0), (_, mc1) in zip(
            s, s[1:], m, m[1:], strict=False):
        if (sd1 - sd0).days <= 10:
            rs.append(sc1 / sc0 - 1.0)
            rm.append(mc1 / mc0 - 1.0)
    return rs, rm


def compute_beta(symbol: str, stock: list[Bar], benchmark: list[Bar],
                 window_start: date, window_end: date,
                 min_obs: int = MIN_OBSERVATIONS) -> BetaResult:
    rs, rm = _paired_weekly_returns(
        [b for b in stock if window_start <= b.day <= window_end],
        [b for b in benchmark if window_start <= b.day <= window_end])
    n = len(rs)
    if n < min_obs:
        raise InsufficientPriceHistoryError(symbol, n, min_obs)

    mean_s = sum(rs) / n
    mean_m = sum(rm) / n
    cov = sum((a - mean_s) * (b - mean_m) for a, b in zip(rs, rm, strict=True)) / n
    var_m = sum((b - mean_m) ** 2 for b in rm) / n
    var_s = sum((a - mean_s) ** 2 for a in rs) / n
    raw = cov / var_m
    r_squared = (cov * cov) / (var_m * var_s) if var_s > 0 else 0.0

    return BetaResult(
        raw=raw,
        adjusted=BLUME_WEIGHT * raw + (1.0 - BLUME_WEIGHT),
        n_obs=n,
        r_squared=r_squared,
        window_start=window_start,
        window_end=window_end,
    )
