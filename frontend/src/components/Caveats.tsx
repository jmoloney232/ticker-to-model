/* Every inherited warning, structured, none droppable (non-negotiable #3).
   coverage_low is the hard, non-dismissible warning — marked in rust. */

import type { Check, Warning } from "../types";

const HARD_CODES = new Set(["coverage_low"]);
const GROUP_THRESHOLD = 4; // repeats of one code fold into a disclosure

function Row({ w }: { w: Warning }) {
  return (
    <div className="caveat-row">
      <span
        className={`glyph${HARD_CODES.has(w.code) ? " hard" : ""}`}
        aria-hidden
      >
        △
      </span>
      <span className="text">{w.message}</span>
      <span className="origin">{w.origin}</span>
    </div>
  );
}

export function Caveats({
  warnings,
  checks,
}: {
  warnings: Warning[];
  checks: Check[];
}) {
  const failed = checks.filter((c) => c.status === "fail");
  const valid = failed.length === 0;

  /* Nothing is dropped (owner rule) — a code repeating GROUP_THRESHOLD+
     times renders inside a disclosure instead of as a wall. Hard warnings
     never fold. */
  const byCode = new Map<string, Warning[]>();
  for (const w of warnings) {
    const key = `${w.origin}:${w.code}`;
    byCode.set(key, [...(byCode.get(key) ?? []), w]);
  }
  const blocks: ({ kind: "one"; w: Warning } | { kind: "many"; ws: Warning[] })[] =
    [];
  const folded = new Set<string>();
  for (const w of warnings) {
    const key = `${w.origin}:${w.code}`;
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
        Caveats — {warnings.length}
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
          <Row key={i} w={b.w} />
        ) : (
          <details key={i} className="caveat-group">
            <summary>
              <span className="glyph" aria-hidden>
                △
              </span>
              <span>
                {b.ws.length}× {b.ws[0].code.replace(/_/g, " ")}
                {" — "}
                {b.ws[0].item ?? ""} and{" "}
                {new Set(b.ws.map((w) => w.item)).size - 1} other items across{" "}
                {new Set(b.ws.map((w) => w.fiscal_year)).size} fiscal years
              </span>
              <span className="count">expand</span>
            </summary>
            {b.ws.map((w, j) => (
              <Row key={j} w={w} />
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
