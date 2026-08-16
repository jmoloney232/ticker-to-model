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

/* One entry of the server's valuation-methods registry. The list is ordered
   by the server; ids are stable (gordon | exit_multiple | epv | ...). The UI
   iterates and renders this shape only — adding a method server-side needs
   no rendering changes here. */
export interface MethodDetail {
  key: string;
  label: string;
  unit: string;
  value: number;
}

export type MethodOut = {
  id: string;
  label: string;
  family: string; // dcf | epv — which view renders it (server-owned)
  note: string;
} & (
  | {
      available: true;
      value_per_share: number;
      vs_price: number;
      enterprise_value: number;
      equity_value: number;
      detail: MethodDetail[];
      bridge: BridgeItem[];
    }
  | { available: false; reason: Reason }
);

export function findMethod(
  valuation: MethodOut[],
  id: string,
): MethodOut | undefined {
  return valuation.find((mo) => mo.id === id);
}

export function methodDetail(
  mo: MethodOut | undefined,
  key: string,
): number | null {
  if (!mo || !mo.available) return null;
  return mo.detail.find((d) => d.key === key)?.value ?? null;
}

export interface GrowthOut {
  available: boolean;
  state: string; // positive | value_destructive | unavailable
  text: string; // server-written sentence — never composed client-side
  epv_text: string; // the same number phrased from the EPV view's side
  per_share?: number | null;
  share_of_dcf?: number | null;
  reason?: Reason;
}

/* A view family: the DCF/EPV selector renders these — id, label, blurb,
   and (for EPV) the exact assumption names the view exposes. Server-owned
   and perturbation-tested; fields: null means the full surface. */
export interface Family {
  id: string;
  label: string;
  blurb: string;
  fields: string[] | null;
}

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

export interface ProfileInfo {
  tag: string; // "compounder+reinvestment_heavy"
  primary: "compounder" | "mature" | "declining";
  modifiers: string[];
  reassigned: boolean;
  notes: string[];
  measures: {
    cagr: number;
    g_latest: number;
    roic_median: number | null;
    roic_years_above_wacc: number;
    roic_years: number;
    wacc: number;
    margin_range: number;
    rev_down_years: number;
    capex_da: number | null;
    window: number;
  };
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
  profile: ProfileInfo | null;
  assumptions: AssumptionRow[];
  provenance_counts: { derived: number; preset: number; user: number };
  verdict: Verdict;
  curves: Record<string, Curve>;
  drivers: Driver[];
  valuation: MethodOut[];
  families: Family[];
  growth: GrowthOut;
  epv_verdict: Verdict;
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
