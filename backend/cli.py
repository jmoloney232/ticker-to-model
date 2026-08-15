"""Ticker → valuation summary in the terminal. The phase 2 deliverable:
everything the dashboard will eventually show, evaluable without a UI.

Usage:
    python -m cli MSFT
    python -m cli MSFT --set terminal_growth=0.02 --set midyear=false
    python -m cli MSFT --preset street_convention
    python -m cli --list-presets
    python -m cli MSFT --preset downside --encode-set   # shareable code
    python -m cli MSFT --set-code <CODE>
    python -m cli MSFT --json > msft.json
    python -m cli MSFT --valuation-date 2026-08-14

Every warning inherited from ingest and market data appears in the output —
a model built on a derived EBIT, a derived share count, or an immaterial cash
residual says so here, not in a log.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import date
from pathlib import Path

from engine.assumptions import derive_assumptions
from engine.dcf import build_model
from engine.errors import EngineError
from engine.models import Bridge, ModelResult, SensitivityGrid
from engine.presets import (
    Preset,
    apply_preset,
    decode_assumption_set,
    encode_assumption_set,
    load_presets,
)
from ingest.assemble import build_financial_history
from ingest.cache import SqliteCache
from ingest.edgar import EdgarClient
from ingest.errors import IngestError
from market.alpaca import AlpacaClient
from market.assemble import build_market_inputs
from market.errors import MarketDataError
from market.fred import FredClient
from market.provider import LadderedProvider, market_today

ENV_PATH = Path(__file__).parent.parent / ".env"


def load_dotenv(path: Path = ENV_PATH) -> None:
    """Minimal .env loader — existing environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_override(text: str) -> tuple[str, float | bool]:
    if "=" not in text:
        raise SystemExit(f"--set expects name=value, got {text!r}")
    name, _, raw = text.partition("=")
    lowered = raw.strip().lower()
    if lowered in ("true", "false"):
        return name.strip(), lowered == "true"
    try:
        return name.strip(), float(raw)
    except ValueError:
        raise SystemExit(f"--set {name}: {raw!r} is not a number or true/false") from None


# ── formatting ───────────────────────────────────────────────────────────────

def money(v: float) -> str:
    a = abs(v)
    if a >= 1e12:
        return f"${v / 1e12:,.2f}T"
    if a >= 1e9:
        return f"${v / 1e9:,.1f}B"
    if a >= 1e6:
        return f"${v / 1e6:,.0f}M"
    return f"${v:,.0f}"


def fmt(value, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "flag":
        return "on" if value else "off"
    if unit in ("rate", "ratio"):
        return f"{value:.2%}"
    if unit == "days":
        return f"{value:.1f}d"
    if unit == "x":
        return f"{value:.2f}x"
    if unit == "usd":
        return money(value)
    if unit == "shares":
        return f"{value / 1e9:.3f}B" if value >= 1e9 else f"{value:,.0f}"
    return str(value)


def _grid_lines(g: SensitivityGrid, col_fmt) -> list[str]:
    out = [f"  {g.row_label} \\ {g.col_label:>12} "
           + "".join(f"{col_fmt(c):>10}" for c in g.cols)]
    for wacc, row in zip(g.rows, g.cells, strict=True):
        cells = "".join(f"{('—' if c is None else f'{c:,.2f}'):>10}" for c in row)
        out.append(f"  {wacc:>7.2%}{'':>13}{cells}")
    return out


def _bridge_lines(b: Bridge) -> list[str]:
    out = [f"  Enterprise value{'':>21}{money(b.enterprise_value):>12}"]
    additions = ("excess_cash", "long_term_investments")
    for item in b.items:
        label = item.name.replace("_", " ")
        note = "  [unmapped — 0, see warnings]" if item.source == "zero_logged" else ""
        sign = "+" if item.name in additions else "−"
        out.append(f"    {sign} {label:<32}{money(abs(item.value)):>12}{note}")
    out.append(f"  Equity value{'':>25}{money(b.equity_value):>12}")
    out.append(f"  ÷ shares {b.shares / 1e9:,.3f}B  →  "
               f"value per share ${b.value_per_share:,.2f}")
    return out


def render(m: ModelResult, preset: Preset | None = None) -> str:
    L: list[str] = []
    h, mkt = m.history, m.market
    price = mkt.price.value

    L.append("=" * 78)
    L.append(f"{h.company.name} ({m.ticker}) — DCF valuation")
    L.append(f"Price ${price:,.2f} (as of {mkt.price.as_of}, "
             f"{mkt.price.staleness}) · Valuation date {m.valuation_date} · "
             f"History FY{h.periods[0].fiscal_year}–FY{h.periods[-1].fiscal_year} "
             f"({h.cost_structure})")
    active = m.assumptions.active_preset
    if active and active != "derived":
        title = preset.title if preset is not None else active
        L.append(f"Preset: {title} — "
                 + (preset.rationale if preset is not None else "see presets.yaml"))
    L.append("=" * 78)

    L.append("")
    L.append("VALUE PER SHARE")
    if not m.bridges:
        L.append("  unavailable — negative terminal anchors on every leg "
                 "(see warnings); the reverse DCF remains informative")
    for method, bridge in m.bridges.items():
        delta = bridge.value_per_share / price - 1
        L.append(f"  {method:<14} ${bridge.value_per_share:>10,.2f}   "
                 f"vs price ${price:,.2f}  ({delta:+.0%})")
    if "implied_exit_multiple" in m.crosschecks:
        L.append(f"  Cross-check: your terminal g of "
                 f"{m.assumptions.eff('terminal_growth'):.2%} implies an exit "
                 f"multiple of {m.crosschecks['implied_exit_multiple']:.1f}x")
    if "implied_terminal_g" in m.crosschecks:
        L.append(f"               your exit multiple of "
                 f"{m.assumptions.eff('exit_multiple'):.1f}x implies terminal g "
                 f"of {m.crosschecks['implied_terminal_g']:.2%}")

    # warnings — every stream, loudly, near the top (owner requirement).
    # unmapped_item repeats per (item, year); group by item with the years
    # aggregated so 55 lines of the same fact become one line per item —
    # nothing dropped, everything readable.
    L.append("")
    all_w = m.all_warnings()
    L.append(f"WARNINGS ({len(all_w)})" if all_w else "WARNINGS (none)")
    unmapped: dict[str, list[int]] = {}
    unmapped_msg: dict[str, str] = {}
    for w in h.warnings:
        if w.code == "unmapped_item" and w.item:
            unmapped.setdefault(w.item, []).append(w.fiscal_year)
            unmapped_msg[w.item] = w.message
    for origin, code, message in all_w:
        if origin == "ingest" and code == "unmapped_item":
            continue
        L.append(f"  [{origin}:{code}] {message}")
    for item, years in unmapped.items():
        tried = unmapped_msg[item].partition("(tried: ")[2].rpartition(")")[0]
        span = (f"FY{min(y for y in years if y)}–FY{max(y for y in years if y)}"
                if len(years) > 1 else f"FY{years[0]}")
        L.append(f"  [ingest:unmapped_item] {item} unmapped in {span} "
                 f"(tried: {tried}); treated as 0")
    L.append(f"  Historical validation: {h.validation.overall}"
             + (f" — {h.coverage.assets_named_share:.0%} of assets / "
                f"{h.coverage.liabilities_named_share:.0%} of liabilities mapped"
                if h.coverage else ""))
    for r in h.validation.results:
        if r.status in ("warn", "fail"):
            L.append(f"    {r.check_id} {r.status}: {r.detail[:110]}")
    proj_flags = [f"{r.check_id}={r.status}" for r in m.checks.results]
    L.append("  Projection checks: " + "  ".join(proj_flags))
    for r in m.checks.results:
        if r.status == "warn":
            L.append(f"    {r.check_id}: {r.detail[:110]}")

    # income statement: historicals + projections
    L.append("")
    L.append("INCOME STATEMENT ($B; E = projected)")
    hist = h.periods[-3:]
    cols = ([f"FY{p.fiscal_year}" for p in hist]
            + [f"FY{p.fiscal_year}E" for p in m.projections])
    L.append("  " + f"{'':<22}" + "".join(f"{c:>9}" for c in cols))
    rows = [("revenue", "Revenue"), ("gross_profit", "Gross profit"),
            ("operating_income", "EBIT"), ("pretax_income", "Pretax"),
            ("net_income", "Net income")]
    for key, label in rows:
        vals = []
        skip = False
        for p in hist:
            v = p.value(key)
            if v is None:
                skip = key == "gross_profit"      # by-nature: absent, never zero
                break
            vals.append(v)
        if skip:
            continue
        vals += [pp.income.get(key) for pp in m.projections]
        cells = "".join(f"{(v / 1e9):>9,.1f}" if v is not None else f"{'—':>9}"
                        for v in vals)
        L.append(f"  {label:<22}{cells}")

    # assumptions echo — the defensible part; every value with its derivation
    # AND its provenance (derived | preset:<name> | user), owner requirement:
    # same principle as distinguishing an unmapped zero from a genuine zero
    L.append("")
    L.append("ASSUMPTIONS (provenance: ' ' = derived, p = preset, * = user override)")
    for a in m.assumptions.fields.values():
        prov = a.provenance
        mark = "*" if prov == "user" else ("p" if prov != "derived" else " ")
        if prov == "user":
            desc = f"USER (derived default {fmt(a.value, a.unit)}) — {a.derivation}"
        elif prov != "derived":
            desc = (f"{prov} [{a.preset_note}] — "
                    f"derived default {fmt(a.value, a.unit)}")
        else:
            desc = a.derivation
        L.append(f" {mark}{a.name:<24}{fmt(a.effective, a.unit):>12}   {desc}")

    # WACC build-up
    w = m.wacc
    L.append("")
    L.append("WACC BUILD-UP")
    L.append(f"  Cost of equity: rf {w.risk_free:.2%} + β {w.beta_used:.3f} "
             f"({w.beta_source}) × ERP {w.erp:.2%} = {w.cost_of_equity:.2%}")
    cov = f"{w.coverage:.1f}x" if w.coverage is not None else "n/a (no interest)"
    L.append(f"  Cost of debt ({w.kd_method}): coverage {cov} → {w.rating} "
             f"spread {w.spread:.2%} → Kd {w.kd_pretax:.2%} × (1−{w.marginal_tax:.0%})"
             f" = {w.kd_after_tax:.2%}")
    L.append(f"  Weights: E {money(w.market_cap)} ({w.weight_equity:.1%}) / "
             f"D {money(w.gross_debt)} ({w.weight_debt:.1%}, gross book debt)")
    L.append(f"  WACC = {w.wacc:.2%}")

    # UFCF schedule
    L.append("")
    L.append("UNLEVERED FREE CASH FLOW ($B)")
    L.append(f"  {'FY':<8}{'EBIT':>8}{'NOPAT':>8}{'D&A':>7}{'capex':>8}"
             f"{'ΔNWC':>8}{'UFCF':>8}{'t':>7}{'PV':>9}")
    for y in m.ufcf:
        L.append(f"  {y.fiscal_year:<8}{y.ebit / 1e9:>8,.1f}{y.nopat / 1e9:>8,.1f}"
                 f"{y.d_and_a / 1e9:>7,.1f}{y.capex / 1e9:>8,.1f}"
                 f"{y.delta_nwc / 1e9:>8,.1f}{y.ufcf / 1e9:>8,.1f}"
                 f"{y.exponent:>7.2f}{y.pv / 1e9:>9,.1f}")

    # terminal values
    L.append("")
    L.append("TERMINAL VALUE")
    if "gordon" in m.terminal:
        gordon = m.terminal["gordon"]
        d = gordon.detail
        L.append(f"  Gordon: NOPAT(N+1) {money(d['nopat_n1'])} × (1 − RR "
                 f"{d['reinvestment_rate']:.1%} [g {d['g']:.2%} ÷ ROIC {d['roic']:.1%}])"
                 f" ÷ (WACC − g) = {money(gordon.value_at_fyeN)} → PV {money(gordon.pv)}"
                 f" (t={gordon.exponent:.2f})")
    else:
        L.append("  Gordon: unavailable — negative terminal NOPAT anchor "
                 "(see warnings)")
    if "exit_multiple" in m.terminal:
        e = m.terminal["exit_multiple"]
        L.append(f"  Exit:   {e.detail['multiple']:.1f}x × EBITDA(N) "
                 f"{money(e.detail['ebitda_n'])} = {money(e.value_at_fyeN)} → "
                 f"PV {money(e.pv)} (t={e.exponent:.2f}, full period — a sale is "
                 "a year-end event)")

    # bridges
    for method, bridge in m.bridges.items():
        L.append("")
        L.append(f"EV → EQUITY ({method})")
        L.extend(_bridge_lines(bridge))

    # sensitivity
    for name, grid in m.sensitivity.items():
        L.append("")
        L.append(f"SENSITIVITY — value per share ({name})")
        col_fmt = ((lambda c: f"{c:.2%}") if name == "wacc_x_g"
                   else (lambda c: f"{c:.1f}x"))
        L.extend(_grid_lines(grid, col_fmt))

    L.append("")
    return "\n".join(L)


# ── wiring ───────────────────────────────────────────────────────────────────

def run(ticker: str, edgar_source, market_provider,
        overrides: dict | None = None, valuation_date: date | None = None,
        as_json: bool = False, preset_name: str | None = None,
        excel_path: str | None = None) -> str:
    vd = valuation_date or market_today()
    history = build_financial_history(ticker, edgar_source)
    market = build_market_inputs(ticker, market_provider, as_of=vd)
    # strict layering (owner rule): derive -> preset -> user overrides
    preset = assumptions = None
    if preset_name:
        presets = load_presets()
        if preset_name not in presets:
            raise SystemExit(f"unknown preset {preset_name!r} "
                             f"(available: {', '.join(presets)})")
        preset = presets[preset_name]
        assumptions = apply_preset(derive_assumptions(history, market), preset,
                                   history, market, vd)
    result = build_model(history, market, valuation_date=vd,
                         overrides=overrides, assumptions=assumptions)
    if excel_path:
        from excel import write_workbook  # openpyxl import deferred to use
        write_workbook(result, excel_path, preset=preset)
    if as_json:
        return json.dumps(dataclasses.asdict(result), default=str, indent=2)
    out = render(result, preset=preset)
    if excel_path:
        out += f"\nWorkbook written: {excel_path}\n"
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m cli",
        description="Ticker → DCF valuation summary (phase 2 CLI)")
    parser.add_argument("ticker", nargs="?")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                        help="override an assumption (repeatable)")
    parser.add_argument("--preset", default=None, metavar="NAME",
                        help="run under a named assumption preset "
                             "(see --list-presets)")
    parser.add_argument("--list-presets", action="store_true",
                        help="list available presets with their rationales")
    parser.add_argument("--set-code", default=None, metavar="CODE",
                        help="apply a compact assumption-set code "
                             "(explicit --preset/--set win on conflict)")
    parser.add_argument("--encode-set", action="store_true",
                        help="print the compact code for this preset+overrides")
    parser.add_argument("--excel", default=None, metavar="PATH",
                        help="write the formula-driven workbook to PATH "
                             "(same preset/override layering as the terminal "
                             "output)")
    parser.add_argument("--json", action="store_true",
                        help="dump the full ModelResult as JSON")
    parser.add_argument("--valuation-date", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)

    if args.list_presets:
        for p in load_presets().values():
            print(f"{p.name:<20} {p.title}")
            print(f"{'':<20} {p.rationale}")
        return 0
    if not args.ticker:
        parser.error("ticker is required (or use --list-presets)")

    load_dotenv()
    user_agent = os.environ.get("EDGAR_USER_AGENT", "")
    if not user_agent:
        print("EDGAR_USER_AGENT is not set (see .env.example)", file=sys.stderr)
        return 2
    cache = SqliteCache(os.environ.get("CACHE_DB_PATH", "cache.sqlite"))
    edgar = EdgarClient(user_agent=user_agent, cache=cache)
    provider = LadderedProvider(
        AlpacaClient(os.environ.get("ALPACA_API_KEY_ID", ""),
                     os.environ.get("ALPACA_API_SECRET_KEY", "")),
        FredClient(os.environ.get("FRED_API_KEY", "")),
        cache=cache)
    code_preset, code_overrides, _code_profile = (decode_assumption_set(args.set_code)
                                   if args.set_code else (None, {}))
    overrides = {**code_overrides, **dict(parse_override(s) for s in args.set)}
    preset_name = args.preset or code_preset

    try:
        print(run(args.ticker, edgar, provider, overrides or None,
                  args.valuation_date, args.json, preset_name=preset_name,
                  excel_path=args.excel))
    except (IngestError, MarketDataError, EngineError) as exc:
        print(exc.user_message, file=sys.stderr)
        return 1
    if args.encode_set:
        print("Assumption-set code:",
              encode_assumption_set(preset_name, overrides or None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
