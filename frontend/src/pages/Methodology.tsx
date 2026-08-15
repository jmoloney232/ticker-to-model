/* Every convention, default, and derivation rule, rendered from
   GET /api/methodology (methodology.yaml + presets.yaml). A product
   requirement (non-negotiable #5): adding a preset or convention on the
   backend appears here with no frontend change. The body is exported so the
   Audit tab renders the same content inline. */

import { useEffect, useState } from "react";
import { navigate } from "../App";
import { fetchMethodology } from "../api";
import type { MethodologyDoc } from "../types";

const CATEGORY_ORDER = [
  "cash_flow",
  "discount_rate",
  "terminal_value",
  "bridge",
  "assumptions",
  "mechanics",
  "data",
];

function catLabel(c: string): string {
  return c.replace(/_/g, " ");
}

export function MethodologyBody({ doc }: { doc: MethodologyDoc }) {
  const cats = (() => {
    const seen = new Map<string, typeof doc.conventions>();
    for (const c of doc.conventions) {
      const list = seen.get(c.category) ?? [];
      list.push(c);
      seen.set(c.category, list);
    }
    const ordered = [
      ...CATEGORY_ORDER.filter((c) => seen.has(c)),
      ...[...seen.keys()].filter((c) => !CATEGORY_ORDER.includes(c)),
    ];
    return ordered.map((c) => [c, seen.get(c)!] as const);
  })();

  return (
    <>
      {cats.map(([cat, conventions]) => (
        <section key={cat} className="meth-cat">
          <div className="kicker">{catLabel(cat)}</div>
          {conventions.map((c) => (
            <div key={c.id} className="conv">
              <div className="name">
                {c.label}
                <span className="flag">
                  {c.editable ? "editable input" : "fixed convention"}
                </span>
              </div>
              <div className="body">
                <b>Default:</b> {c.default}
                <span className="d">
                  <b>Derivation:</b> {c.derivation}
                </span>
                {c.tradeoff && <span className="t">{c.tradeoff}</span>}
              </div>
            </div>
          ))}
        </section>
      ))}

      <section className="meth-cat">
        <div className="kicker">Assumption presets</div>
        {doc.presets.map((p) => (
          <div key={p.name} className="pcard">
            <div className="prow">
              <span className="pname">{p.title}</span>
              <span className="flag">{p.name}</span>
            </div>
            <div className="pwhy">{p.rationale}</div>
            <div className="pfields">
              {p.fields.map((f) => (
                <div key={f.field} className="pfield">
                  <span className="fname">{f.field}</span>
                  <span className="frule">
                    {f.form === "literal"
                      ? `= ${String(f.value)}`
                      : f.form === "solved"
                        ? `solved: ${f.solver ?? ""} → ${f.target ?? ""}`
                        : (f.rule ?? "")}
                    {f.optional ? " (optional)" : ""}
                    {f.note && <span className="fnote">{f.note}</span>}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </section>
    </>
  );
}

export function Methodology() {
  const [doc, setDoc] = useState<MethodologyDoc | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetchMethodology()
      .then(setDoc)
      .catch((e) => setErr(String(e.message ?? e)));
  }, []);

  return (
    <div className="shell">
      <div className="meth">
        <span className="regmark tl" />
        <span className="regmark br" />
        <div className="kicker">Ticker to Model</div>
        <h1>Methodology</h1>
        <p className="sub">
          Every financial convention, default, and derivation rule used
          anywhere in the model — the same source file renders the workbook&rsquo;s
          Methodology sheet, so the two cannot drift.
        </p>

        {err && <p className="sub">Couldn&rsquo;t load: {err}</p>}
        {!doc && !err && <div className="loading-note">loading…</div>}
        {doc && <MethodologyBody doc={doc} />}
      </div>
      <nav className="foot-nav" style={{ width: 1120 }}>
        <a
          href="/"
          onClick={(e) => {
            e.preventDefault();
            navigate("/");
          }}
        >
          ← Ticker entry
        </a>
      </nav>
    </div>
  );
}
