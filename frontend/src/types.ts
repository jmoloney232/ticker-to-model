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

export interface Verdict {
  text: string;
  state: string; // "ok" | "negative_equity" | "no_gordon" | "no_legs"
}

export interface Curve {
  leg: string;
  domain: [number, number];
  points: [number, number | null][]; // (assumption value, per-share) — engine-computed
  landmarks: {
    derived: number;
    current: number;
    market_implied: number | null;
    rf: number | null;
    block: number;
  };
}

export interface Driver {
  name: string;
  label: string;
  direction: "up" | "down"; // value moves with the input / against it
  step_label: string;
  impact_per_share: number;
  note: string;
  composite: boolean;
  leg: string;
}

export interface DigestEntry {
  text: string;
  codes: string[];
  count: number;
  severity: "warn" | "info";
  hard: boolean;
}

export interface ProjectionRow {
  fiscal_year: number;
  fye: string;
  income: Record<string, number>;
  balance: Record<string, number>;
  cashflow: Record<string, number>;
}

export interface HistoryFact {
  value: number;
  source: string;
  restated: boolean;
}

export interface HistoryPeriod {
  fiscal_year: number;
  end: string;
  is_53_week: boolean;
  income: Record<string, HistoryFact>;
  balance: Record<string, HistoryFact>;
  cashflow: Record<string, HistoryFact>;
}

export interface ModelOk {
  status: "ok";
  ticker: string;
  valuation_date: string;
  code: string;
  company: {
    name: string;
    short_name: string; // prose name, server-derived ("Microsoft")
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
  verdict: Verdict;
  curves: Record<string, Curve>;
  drivers: Driver[];
  valuation: { gordon: Leg; exit_multiple: Leg };
  wacc: Record<string, unknown>;
  ufcf: Record<string, number>[];
  projections: ProjectionRow[];
  crosschecks: Record<string, number | null>;
  sensitivity: Record<string, Grid>;
  checks: Check[];
  warnings: Warning[];
  warnings_digest: DigestEntry[];
  history: HistoryPeriod[];
  coverage: {
    assets_named_share: number;
    liabilities_named_share: number;
    expenses_named_share: number;
  } | null;
  reverse: Record<string, ImpliedSolve> | null;
}

export interface ModelBlocked {
  status: "refused" | "unsupported" | "preset_unavailable";
  verdict?: string; // plain-English sentence (refusals; not preset_unavailable)
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
