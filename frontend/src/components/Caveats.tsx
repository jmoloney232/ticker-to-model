/* Every inherited warning, structured, none droppable (non-negotiable #3).
   coverage_low is the hard, non-dismissible warning — marked in rust.
   severity "info" is a disclosure, not a defect — rendered as a quiet note,
   counted separately, never folded into a warn group. */

import type { Check, Warning } from "../types";

const HARD_CODES = new Set(["coverage_low"]);
const GROUP_THRESHOLD = 4; // repeats of one code fold into a disclosure

const isInfo = (w: Warning) => w.severity === "info";

function Row({ w, showCode }: { w: Warning; showCode?: boolean }) {
  const info = isInfo(w);
  return (
    <div className={`caveat-row${info ? " note" : ""}`}>
      <span
        className={`glyph${HARD_CODES.has(w.code) ? " hard" : ""}${info ? " info" : ""}`}
        aria-hidden
      >
        {info ? "○" : "△"}
      </span>
      <span className="text">{w.message}</span>
      {showCode && <span className="code-chip">{w.code}</span>}
      <span className="origin">
        {w.origin}
        {info ? " · note" : ""}
      </span>
    </div>
  );
}

export function Caveats({
  warnings,
  checks,
  showCodes = false,
}: {
  warnings: Warning[];
  checks: Check[];
  showCodes?: boolean;
}) {
  const failed = checks.filter((c) => c.status === "fail");
  const valid = failed.length === 0;
  const nInfo = warnings.filter(isInfo).length;
  const nWarn = warnings.length - nInfo;
  const countText =
    nWarn > 0 && nInfo > 0
      ? `${nWarn} · ${nInfo} note${nInfo > 1 ? "s" : ""}`
      : nInfo > 0
        ? `${nInfo} note${nInfo > 1 ? "s" : ""}`
        : `${nWarn}`;

  /* Nothing is dropped (owner rule) — a code repeating GROUP_THRESHOLD+
     times renders inside a disclosure instead of as a wall. Hard warnings
     never fold, and the fold key carries severity so an info note never
     folds into a warn group (or vice versa). Notes render after warnings. */
  const ordered = [...warnings.filter((w) => !isInfo(w)), ...warnings.filter(isInfo)];
  const byCode = new Map<string, Warning[]>();
  for (const w of ordered) {
    const key = `${w.origin}:${w.code}:${w.severity}`;
    byCode.set(key, [...(byCode.get(key) ?? []), w]);
  }
  const blocks: ({ kind: "one"; w: Warning } | { kind: "many"; ws: Warning[] })[] =
    [];
  const folded = new Set<string>();
  for (const w of ordered) {
    const key = `${w.origin}:${w.code}:${w.severity}`;
    const group = byCode.get(key)!;
    if (group.length >= GROUP_THRESHOLD && !HARD_CODES.has(w.code)) {
      if (!folded.has(key)) {
        folded.add(key);
        blocks.push({ kind: "many", ws: group });
      }
    } else {
      blocks.push({ kind: "one", w });
    }
  }

  return (
    <div className="caveats">
      <div className={`kicker${valid ? "" : " bad"}`}>
        Caveats — {countText}
        {valid ? ", model valid" : ` · ${failed.length} check(s) FAILED`}
      </div>
      {warnings.length === 0 && valid && (
        <div className="caveat-row">
          <span className="text">
            None. Statements tie and no warning was inherited from ingest,
            market data, or the engine.
          </span>
        </div>
      )}
      {blocks.map((b, i) =>
        b.kind === "one" ? (
          <Row key={i} w={b.w} showCode={showCodes} />
        ) : (
          <details key={i} className="caveat-group">
            <summary>
              <span
                className={`glyph${isInfo(b.ws[0]) ? " info" : ""}`}
                aria-hidden
              >
                {isInfo(b.ws[0]) ? "○" : "△"}
              </span>
              <span>
                {b.ws.length}× {b.ws[0].code.replace(/_/g, " ")}
                {isInfo(b.ws[0]) ? " (notes)" : ""}
                {" — "}
                {b.ws[0].item ?? ""} and{" "}
                {new Set(b.ws.map((w) => w.item)).size - 1} other items across{" "}
                {new Set(b.ws.map((w) => w.fiscal_year)).size} fiscal years
              </span>
              <span className="count">expand</span>
            </summary>
            {b.ws.map((w, j) => (
              <Row key={j} w={w} showCode={showCodes} />
            ))}
          </details>
        ),
      )}
    </div>
  );
}

export function CheckBand({ checks }: { checks: Check[] }) {
  const failed = checks.filter((c) => c.status === "fail");
  if (failed.length === 0) return null;
  return (
    <div className="checkband">
      <span className="kicker">Validation failed</span>
      <span>
        {failed.map((c) => `${c.id}: ${c.detail}`).join(" · ")} — the model is
        shown for inspection but does not tie.
      </span>
    </div>
  );
}
