/* Audit tab — the evidence: every warning with its machine code, the full
   methodology (same source as the /methodology page and the workbook
   sheet), the committed audit guide (deviations register included), and
   the historical statements as filed. Sections expand IN PLACE. */

import { useEffect, useState } from "react";
import { fetchAuditGuide, fetchMethodology } from "../api";
import type { MethodologyDoc, ModelOk } from "../types";
import { MethodologyBody } from "../pages/Methodology";
import { Caveats } from "./Caveats";
import { MiniMarkdown } from "./MiniMarkdown";
import { HistoricalsTable } from "./Statements";

export function AuditTab({ model }: { model: ModelOk }) {
  const [doc, setDoc] = useState<MethodologyDoc | null>(null);
  const [guide, setGuide] = useState<string | null>(null);
  const [guideErr, setGuideErr] = useState<string | null>(null);
  const [methErr, setMethErr] = useState<string | null>(null);

  useEffect(() => {
    fetchMethodology()
      .then(setDoc)
      .catch((e) => setMethErr(String(e.message ?? e)));
    fetchAuditGuide()
      .then((r) => setGuide(r.markdown))
      .catch((e) => setGuideErr(String(e.message ?? e)));
  }, []);

  return (
    <div className="audit">
      <div className="audit-sec">
        <div className="paneband">
          <span className="kicker">
            Every warning, with its machine code — nothing folded away
          </span>
        </div>
        <Caveats warnings={model.warnings} checks={model.checks} showCodes />
      </div>

      <div className="audit-sec">
        <details className="audit-fold">
          <summary>
            <span className="kicker">Historical statements as filed</span>
            <span className="count">expand</span>
          </summary>
          <HistoricalsTable periods={model.history} />
        </details>
      </div>

      <div className="audit-sec">
        <details className="audit-fold">
          <summary>
            <span className="kicker">
              Methodology — every convention and derivation rule
            </span>
            <span className="count">expand</span>
          </summary>
          <div className="audit-meth">
            {methErr && <p className="sub">Couldn&rsquo;t load: {methErr}</p>}
            {doc && <MethodologyBody doc={doc} />}
          </div>
        </details>
      </div>

      <div className="audit-sec">
        <details className="audit-fold">
          <summary>
            <span className="kicker">
              The audit guide — assumptions, section by section, deviations
              register included
            </span>
            <span className="count">expand</span>
          </summary>
          {guideErr && (
            <p className="sub">
              Couldn&rsquo;t load the audit guide here: {guideErr} It ships in the
              repository as docs/financial-assumptions.md.
            </p>
          )}
          {guide && <MiniMarkdown source={guide} />}
        </details>
      </div>
    </div>
  );
}
