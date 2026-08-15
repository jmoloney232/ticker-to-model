/* The API contract (specs/06-webapp.md, backend/app/serialize.py), verbatim.
   The frontend renders these fields; it never re-derives them. */

export interface Reason {
  code: string;
  message: string;
  detail: Record<string, unknown>;
}

export interface AssumptionRow {
  name: string;
  label: string;
  value: number | boolean | null; // null: honestly underivable (e.g. exit multiple with EBITDA ≤ 0)
  unit: string; // "ratio" | "rate" | "x" | "flag" | "days" | "shares" | …
  provenance: string; // "derived" | "preset:<name>" | "user"
  derived_default: number | boolean | null;
  rule: string;
  editable: boolean;
}

export interface Warning {
  origin: "ingest" | "market" | "engine";
  code: string;
  message: string;
  fiscal_year: number | null;
  item: string | null;
  severity: "warn" | "info"; // info = disclosure, not defect
  detail: Record<string, unknown>;
}

export interface BridgeItem {
  name: string;
  value: number;
  source: string;
  note: string | null;
}

export type Leg =
  | {
      available: true;
      value_per_share: number;
      vs_price: number;
      enterprise_value: number;
      equity_value: number;
      tv_at_fyeN: number;
      tv_pv: number;
      tv_exponent: number;
      tv_share_of_ev: number | null;
      tv_detail: Record<string, unknown>;
      bridge: BridgeItem[];
    }
  | { available: false; reason: Reason };

export interface Grid {
  row_label: string;
  col_label: string;
  rows: number[];
  cols: number[];
  cells: (number | null)[][];
}

export interface Check {
  id: string;
  severity: string;
  status: string; // "ok" | "warn" | "fail" | "info" …
  magnitude: number | null;
  detail: string;
}

export interface ImpliedSolve {
  derived: number;
  implied: number | null;
  status: string; // "solved" | "no_solution_below_wacc" | …
  target_price: number;
}

export interface Quote {
  value: number;
  as_of: string;
  staleness: string;
}

export interface ModelOk {
  status: "ok";
  ticker: string;
  valuation_date: string;
  code: string;
  company: {
    name: string;
    cik: string;
    sic: string | null;
    sic_description: string | null;
    fye_anchor: string | null;
    cost_structure: string;
    filing_basis: {
      fiscal_year: number;
      accession: string;
      filed: string | null;
    } | null;
  };
  market: {
    price: Quote;
    risk_free: Quote;
    beta: Record<string, unknown> | null;
  };
  preset: { name: string; title: string; rationale: string } | null;
  assumptions: AssumptionRow[];
  provenance_counts: { derived: number; preset: number; user: number };
  valuation: { gordon: Leg; exit_multiple: Leg };
  wacc: Record<string, unknown>;
  crosschecks: Record<string, number | null>;
  sensitivity: Record<string, Grid>;
  checks: Check[];
  warnings: Warning[];
  coverage: {
    assets_named_share: number;
    liabilities_named_share: number;
    expenses_named_share: number;
  } | null;
  reverse: Record<string, ImpliedSolve> | null;
}

export interface ModelBlocked {
  status: "refused" | "unsupported" | "preset_unavailable";
  reason: Reason;
}

export type ModelResponse = ModelOk | ModelBlocked;

export interface PresetInfo {
  name: string;
  title: string;
  rationale: string;
  builtin: boolean;
  applicability: Record<string, unknown>;
  fields: {
    field: string;
    form: string;
    rule: string | null;
    value: number | boolean | null;
    solver: string | null;
    target: string | null;
    optional: boolean;
    note: string | null; // source + as-of for literals (e.g. Damodaran ERP)
  }[];
}

export interface Convention {
  id: string;
  label: string;
  category: string;
  default: string;
  derivation: string;
  editable: boolean;
  tradeoff: string;
}

export interface MethodologyDoc {
  meta: Record<string, unknown>;
  conventions: Convention[];
  presets: PresetInfo[];
}
