/* Two-column assumptions per 1c: group bands, provenance glyph + source per
   row, edits inline. The hovered row's derivation rule prints in a fixed
   inspector strip at the pane's foot (1a's device, adapted — the mockup's
   "hover a row for the rule behind its default"). */

import { useState } from "react";
import { fmtValue, parseValue, provenanceGlyph, unitSuffix } from "../format";
import type { AssumptionRow } from "../types";

type Matcher = [group: string, test: (r: AssumptionRow) => boolean];

/* Presentation grouping only — an unmatched field lands in "Other" and is
   never dropped. */
const GROUPS: Matcher[] = [
  ["Revenue & growth", (r) => r.name.startsWith("revenue")],
  [
    "Margins & costs",
    (r) =>
      /(^cogs|^rnd|^sga|^other_opex|^unclassified|^sbc)/.test(r.name),
  ],
  ["Working capital", (r) => /(^dso$|^dio$|^dpo$|^oca|^ocl|^accrued|^defrev)/.test(r.name)],
  ["Capital intensity", (r) => /(^capex|^da_pct)/.test(r.name)],
  ["Taxes", (r) => /tax/.test(r.name)],
  [
    "Discount rate",
    (r) =>
      /(^risk_free|^erp|beta|^kd|cost_of_debt|coverage|spread|^embedded_debt|^interest_income)/.test(
        r.name,
      ),
  ],
  [
    "Terminal & horizon",
    (r) => /(^terminal|^exit_multiple|midyear)/.test(r.name),
  ],
  [
    "Shares & bridge",
    (r) => /(^share_count|cash_floor|^excess|^payout)/.test(r.name),
  ],
  ["Conventions", (r) => r.unit === "flag"],
];

function groupRows(rows: AssumptionRow[]): [string, AssumptionRow[]][] {
  const seen = new Set<string>();
  const out: [string, AssumptionRow[]][] = [];
  for (const [name, test] of GROUPS) {
    const hit = rows.filter((r) => !seen.has(r.name) && test(r));
    hit.forEach((r) => seen.add(r.name));
    if (hit.length) out.push([name, hit]);
  }
  const rest = rows.filter((r) => !seen.has(r.name));
  if (rest.length) out.push(["Other", rest]);
  return out;
}

function sourceText(r: AssumptionRow): string {
  if (!r.editable) return "computed";
  if (r.provenance === "user") return "you";
  if (r.provenance.startsWith("preset"))
    return r.provenance.replace("preset:", "preset: ").replace(/_/g, " ");
  return "derived";
}

function Row({
  row,
  onOverride,
  onHover,
}: {
  row: AssumptionRow;
  onOverride: (name: string, value: number | boolean | null) => void;
  onHover: (row: AssumptionRow | null) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const edited = row.provenance === "user";
  const glyphClass = !row.editable
    ? "computed"
    : edited
      ? "edited"
      : row.provenance.startsWith("preset")
        ? "preset"
        : "derived";

  const commit = () => {
    if (draft == null) return;
    const parsed = parseValue(draft, row.unit);
    setDraft(null);
    if (parsed == null || parsed === row.value) return;
    onOverride(row.name, parsed);
  };

  return (
    <div
      className={`arow${edited ? " edited" : ""}`}
      onMouseEnter={() => onHover(row)}
      onMouseLeave={() => onHover(null)}
    >
      <span className="arow-label">{row.label}</span>
      <span className="arow-val">
        {!row.editable || row.value == null ? (
          <span className="locked">{fmtValue(row)}</span>
        ) : row.unit === "flag" ? (
          <button
            type="button"
            className="flagbtn"
            aria-label={`${row.label}: ${row.value ? "yes" : "no"} — toggle`}
            onClick={() => onOverride(row.name, !(row.value as boolean))}
          >
            {row.value ? "yes" : "no"}
          </button>
        ) : (
          <input
            aria-label={row.label}
            value={draft ?? fmtValue(row)}
            onChange={(e) => setDraft(e.target.value)}
            onFocus={(e) => e.target.select()}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              if (e.key === "Escape") setDraft(null);
            }}
          />
        )}
        <span className="unit">{unitSuffix(row.unit)}</span>
      </span>
      <span className="arow-src">
        <span className={`glyph ${glyphClass}`}>
          {provenanceGlyph(row.provenance, row.editable)}
        </span>
        <span className="src-text">{sourceText(row)}</span>
        {edited && (
          <button
            type="button"
            className="reset"
            aria-label={`Reset ${row.label} to its derived default`}
            onClick={() => onOverride(row.name, null)}
          >
            ×
          </button>
        )}
      </span>
    </div>
  );
}

export function Assumptions({
  rows,
  overrideCount,
  overrideError,
  onOverride,
  onResetAll,
}: {
  rows: AssumptionRow[];
  overrideCount: number;
  overrideError: string | null;
  onOverride: (name: string, value: number | boolean | null) => void;
  onResetAll: () => void;
}) {
  const [hovered, setHovered] = useState<AssumptionRow | null>(null);
  const groups = groupRows(rows);
  /* split where the running row count best balances the two columns */
  const total = groups.reduce((n, [, rs]) => n + rs.length + 1, 0);
  let split = groups.length;
  let run = 0;
  for (let i = 0; i < groups.length; i++) {
    run += groups[i][1].length + 1;
    if (run >= total / 2) {
      split = i + 1;
      break;
    }
  }
  const editable = rows.filter((r) => r.editable).length;

  const renderGroups = (gs: [string, AssumptionRow[]][]) =>
    gs.map(([name, rs]) => (
      <div key={name}>
        <div className="ghead">{name}</div>
        {rs.map((r) => (
          <Row key={r.name} row={r} onOverride={onOverride} onHover={setHovered} />
        ))}
      </div>
    ));

  return (
    <div>
      <div className="paneband">
        <span className="kicker">
          Assumptions — {editable} fields, every one editable
        </span>
        <span className="hint">
          {overrideCount > 0 ? (
            <button type="button" onClick={onResetAll}>
              reset all {overrideCount} edits
            </button>
          ) : (
            "hover a row for the rule behind its default"
          )}
        </span>
      </div>
      {overrideError && <div className="override-err">{overrideError}</div>}
      <div className="agrid">
        <div className="acol-l">{renderGroups(groups.slice(0, split))}</div>
        <div>{renderGroups(groups.slice(split))}</div>
      </div>
      <div className="inspector">
        <span className="kicker">
          {hovered
            ? `Rule behind ${hovered.provenance === "user" ? "the edited" : "the"} default — ${hovered.label}`
            : "Rule inspector"}
        </span>
        <div className="rule-text">
          {hovered
            ? hovered.provenance === "user"
              ? `${hovered.rule} (derived default: ${fmtValue({ ...hovered, value: hovered.derived_default })}${unitSuffix(hovered.unit)})`
              : hovered.rule
            : "Hover an assumption to see the derivation rule behind its value."}
        </div>
      </div>
    </div>
  );
}
