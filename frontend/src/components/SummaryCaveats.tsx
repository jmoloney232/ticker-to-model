/* Summary-tab caveats: the server's plain-sentence digest, nothing dropped
   (every entry carries its count; the expander reveals the full structured
   list IN PLACE — no navigation, no lost scroll). coverage_low arrives
   first and hard from the serializer. */

import { useState } from "react";
import type { Check, DigestEntry, Warning } from "../types";
import { Caveats } from "./Caveats";

export function SummaryCaveats({
  digest,
  warnings,
  checks,
}: {
  digest: DigestEntry[];
  warnings: Warning[];
  checks: Check[];
}) {
  const [expanded, setExpanded] = useState(false);
  const failed = checks.filter((c) => c.status === "fail").length;
  const nInfo = digest.filter((e) => e.severity === "info")
    .reduce((n, e) => n + e.count, 0);
  const nWarn = warnings.length - nInfo;

  return (
    <div className="quiet">
      <div className="kicker">
        Caveats — {nWarn}
        {nInfo > 0 ? ` · ${nInfo} note${nInfo > 1 ? "s" : ""}` : ""}
        {failed === 0 ? ", model valid" : ` · ${failed} check(s) FAILED`}
      </div>
      {digest.length === 0 && (
        <div className="caveat-line">
          <span>
            None. Statements tie and no warning was inherited from ingest,
            market data, or the engine.
          </span>
        </div>
      )}
      {digest.map((e, i) => (
        <div className="caveat-line" key={i}>
          <span
            className={`glyph${e.severity === "info" ? " note" : ""}${e.hard ? " hard" : ""}`}
            aria-hidden
          >
            {e.severity === "info" ? "○" : "△"}
          </span>
          <span>{e.text}</span>
          {e.count > 1 && <span className="count-chip">{e.count}×</span>}
        </div>
      ))}
      {warnings.length > 0 && (
        <button
          type="button"
          className="more"
          onClick={() => setExpanded((x) => !x)}
        >
          {expanded
            ? "fold back to summaries"
            : `show all ${warnings.length} individually`}
        </button>
      )}
      {expanded && <Caveats warnings={warnings} checks={checks} />}
    </div>
  );
}
