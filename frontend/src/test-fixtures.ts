/* Compact contract fixtures for component tests — shaped exactly like
   backend/app/serialize.py output. */

import type { AssumptionRow, Grid, ModelOk, Warning } from "./types";

export function row(over: Partial<AssumptionRow> = {}): AssumptionRow {
  return {
    name: "revenue_growth_fy1",
    label: "Revenue growth fy1",
    value: 0.1612,
    unit: "ratio",
    provenance: "derived",
    derived_default: 0.1612,
    rule: "3y revenue CAGR, capped at 30%",
    editable: true,
    ...over,
  };
}

export function warning(over: Partial<Warning> = {}): Warning {
  return {
    origin: "ingest",
    code: "optional_unmapped",
    message: "FY2022: interest_income unmapped; treated as 0.",
    fiscal_year: 2022,
    item: "interest_income",
    severity: "warn",
    detail: {},
    ...over,
  };
}

export function grid(): Grid {
  const rows = [0.0898, 0.0948, 0.0998, 0.1048, 0.1098];
  const cols = [
    0.015, 0.0175, 0.02, 0.0225, 0.025, 0.0275, 0.03, 0.0325, 0.035,
  ];
  return {
    row_label: "WACC",
    col_label: "terminal g",
    rows,
    cols,
    cells: rows.map((_, i) => cols.map((_, j) => 200 + i * 10 + j)),
  };
}

export function modelOk(over: Partial<ModelOk> = {}): ModelOk {
  return {
    status: "ok",
    ticker: "MSFT",
    valuation_date: "2026-08-14",
    code: "abc123",
    company: {
      name: "MICROSOFT CORP",
      short_name: "Microsoft",
      cik: "789019",
      sic: "7372",
      sic_description: "Services-Prepackaged Software",
      fye_anchor: "06-30",
      cost_structure: "by_function",
      filing_basis: { fiscal_year: 2026, accession: "0000-26-1", filed: "2026-07-29" },
    },
    market: {
      price: { value: 498.37, as_of: "2026-08-14", staleness: "snapshot" },
      risk_free: { value: 0.0468, as_of: "2026-08-14", staleness: "snapshot" },
      beta: null,
    },
    preset: null,
    verdict: {
      text:
        "At 2.5% long-run growth, Microsoft is worth $281 a share — 44% " +
        "below its $498 price. The market is pricing in 6.2% growth " +
        "forever — on this model's other assumptions.",
      state: "ok",
    },
    curves: {
      terminal_growth: {
        leg: "gordon",
        domain: [-0.02, 0.0973],
        points: [
          [-0.02, 231.5],
          [0.005, 255.1],
          [0.025, 280.99],
          [0.0468, 330.4],
          [0.0624, 498.37],
          [0.08, 640.2],
          [0.0973, 812.7],
        ],
        landmarks: {
          derived: 0.025,
          current: 0.025,
          market_implied: 0.0624,
          rf: 0.0468,
          block: 0.0973,
        },
      },
    },
    drivers: [
      {
        name: "wacc",
        label: "Discount rate (WACC)",
        direction: "down",
        step_label: "±1pp",
        impact_per_share: 37.2,
        note: "set by beta, ERP and the 10Y · future cash is worth less today",
        composite: true,
        leg: "gordon",
      },
      {
        name: "terminal_growth",
        label: "Long-run growth (terminal g)",
        direction: "up",
        step_label: "±1pp",
        impact_per_share: 30.1,
        note: "compounds forever in the terminal value",
        composite: false,
        leg: "gordon",
      },
    ],
    warnings_digest: [],
    ufcf: [],
    projections: [],
    history: [],
    assumptions: [
      row(),
      row({ name: "terminal_growth", label: "Terminal growth", value: 0.025 }),
      row({ name: "exit_multiple", label: "Exit multiple", value: 18.93, unit: "x" }),
      row({ name: "share_count", label: "Share count", value: 7.45e9, unit: "shares" }),
      row({
        name: "revenue_cagr_uncapped",
        label: "Revenue cagr uncapped",
        value: 0.1612,
        editable: false,
      }),
    ],
    provenance_counts: { derived: 4, preset: 0, user: 0 },
    valuation: {
      gordon: {
        available: true,
        value_per_share: 280.99,
        vs_price: -0.4362,
        enterprise_value: 2.1e12,
        equity_value: 2.09e12,
        tv_at_fyeN: 2.4e12,
        tv_pv: 1.55e12,
        tv_exponent: 4.63,
        tv_share_of_ev: 0.74,
        tv_detail: {},
        bridge: [],
      },
      exit_multiple: {
        available: true,
        value_per_share: 606.28,
        vs_price: 0.2165,
        enterprise_value: 4.6e12,
        equity_value: 4.52e12,
        tv_at_fyeN: 4.9e12,
        tv_pv: 4.05e12,
        tv_exponent: 5.13,
        tv_share_of_ev: 0.88,
        tv_detail: {},
        bridge: [],
      },
    },
    wacc: { wacc: 0.0998, beta_used: 1.07 },
    crosschecks: {},
    sensitivity: { wacc_x_g: grid() },
    checks: [
      { id: "P1", severity: "hard", status: "ok", magnitude: 0, detail: "" },
    ],
    warnings: [],
    coverage: {
      assets_named_share: 1.0,
      liabilities_named_share: 1.0,
      expenses_named_share: 0.99,
    },
    reverse: {
      terminal_growth: {
        derived: 0.025,
        implied: 0.0624,
        status: "solved",
        target_price: 498.37,
      },
    },
    ...over,
  } as ModelOk;
}
