/* "What changed" panel (Part 3, owner spec 2026-08-17): switching a preset
   or reassigning the profile shows WHICH assumptions moved, from what to
   what — the point is that the user understands the lens they picked, not
   just a new number. The diff is presentation-only: both sides are the
   server's own assumption rows; no valuation math lives here. */

import { fmtValue, unitSuffix } from "../format";
import type { AssumptionRow } from "../types";

export interface ChangeRow {
  name: string;
  label: string;
  from: string;
  to: string;
}

export function diffAssumptions(
  prev: AssumptionRow[],
  next: AssumptionRow[],
): ChangeRow[] {
  const before = new Map(prev.map((r) => [r.name, r]));
  const out: ChangeRow[] = [];
  for (const r of next) {
    const b = before.get(r.name);
    if (!b) continue;
    const a = b.value;
    const z = r.value;
    const same =
      a === z ||
      (typeof a === "number" &&
        typeof z === "number" &&
        Math.abs(z - a) <= 1e-9 * Math.max(1, Math.abs(a)));
    if (!same) {
      const sfx = unitSuffix(r.unit);
      out.push({
        name: r.name,
        label: r.label,
        from: `${fmtValue(b)}${sfx}`,
        to: `${fmtValue(r)}${sfx}`,
      });
    }
  }
  return out;
}

export function WhatChanged({
  cause,
  rows,
  onDismiss,
}: {
  cause: string;
  rows: ChangeRow[];
  onDismiss: () => void;
}) {
  return (
    <div className="changes-band" role="status">
      <div className="changes-head">
        <span className="kicker">What changed</span>
        <span className="changes-cause">{cause}</span>
        <button type="button" className="changes-x" onClick={onDismiss}>
          dismiss
        </button>
      </div>
      {rows.length === 0 ? (
        <div className="changes-none">
          No assumption moved — this lens derives the same numbers here.
        </div>
      ) : (
        <ul className="changes-list">
          {rows.map((c) => (
            <li key={c.name}>
              <span className="changes-label">{c.label}</span>
              <span className="changes-move">
                {c.from} → {c.to}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
