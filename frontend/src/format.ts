/* Display formatting only — unit presentation for numbers the API computed.
   No valuation math lives here or anywhere else in the frontend. */

import type { AssumptionRow } from "./types";

export function fmtValue(row: AssumptionRow): string {
  if (row.value == null) return "—";
  if (row.unit === "flag") return row.value ? "yes" : "no";
  const v = row.value as number;
  switch (row.unit) {
    case "ratio":
    case "rate":
      return (v * 100).toFixed(2);
    case "x":
      return v.toFixed(2);
    case "days":
      return v.toFixed(1);
    case "shares":
      return (v / 1e9).toFixed(2);
    default:
      return v.toFixed(2);
  }
}

export function unitSuffix(unit: string): string {
  switch (unit) {
    case "ratio":
    case "rate":
      return "%";
    case "x":
      return "×";
    case "days":
      return "days";
    case "shares":
      return "B sh";
    default:
      return "";
  }
}

/** Parse an edited display string back to engine-native units. */
export function parseValue(text: string, unit: string): number | null {
  const n = Number(text);
  if (!Number.isFinite(n)) return null;
  switch (unit) {
    case "ratio":
    case "rate":
      return n / 100;
    case "shares":
      return n * 1e9;
    default:
      return n;
  }
}

export function fmtPct(v: number | null | undefined, dp = 2): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(dp)}%`;
}

export function fmtSignedPct(v: number | null | undefined, dp = 1): string {
  if (v == null) return "—";
  const s = (v * 100).toFixed(dp);
  return v >= 0 ? `+${s}%` : `−${Math.abs(v * 100).toFixed(dp)}%`;
}

export function fmtPrice(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export const GLYPH = {
  derived: "■", // ■
  preset: "□", // □
  edited: "●", // ●
  computed: "ƒ", // ƒ
  caveat: "△", // △
  inactive: "○", // ○
} as const;

export function provenanceGlyph(prov: string, editable: boolean): string {
  if (!editable) return GLYPH.computed;
  if (prov === "user") return GLYPH.edited;
  if (prov.startsWith("preset")) return GLYPH.preset;
  return GLYPH.derived;
}
