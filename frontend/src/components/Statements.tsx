/* Statement tables — Model tab's projections and Audit tab's historicals.
   Pure presentation of server values: scaling to $M/$B is display
   formatting, never derivation. Restated facts carry a dagger. */

import type { HistoryPeriod, ProjectionRow } from "../types";

function fmtMoney(v: number | null | undefined): string {
  if (v == null) return "—";
  const b = v / 1e9;
  if (Math.abs(b) >= 1) return b.toLocaleString("en-US", {
    minimumFractionDigits: 1, maximumFractionDigits: 1,
  });
  return (v / 1e6).toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function unitOf(v: number | null | undefined): string {
  return v != null && Math.abs(v) >= 1e9 ? "B" : "M";
}

const STATEMENTS = [
  ["income", "Income statement"],
  ["balance", "Balance sheet"],
  ["cashflow", "Cash flow"],
] as const;

function itemLabel(item: string): string {
  return item.replace(/_/g, " ");
}

export function ProjectionsTable({ rows }: { rows: ProjectionRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="stmt-block">
      {STATEMENTS.map(([stmt, label]) => {
        const items = Object.keys(rows[0][stmt]);
        return (
          <details key={stmt} className="stmt" open={stmt === "income"}>
            <summary>
              <span className="kicker">Projected {label.toLowerCase()}</span>
              <span className="count">FY{rows[0].fiscal_year}–FY
                {rows[rows.length - 1].fiscal_year}</span>
            </summary>
            <div className="stmt-tablewrap">
              <table>
                <thead>
                  <tr>
                    <th className="item">$ {unitOf(rows[0][stmt][items[0]])}</th>
                    {rows.map((r) => (
                      <th key={r.fiscal_year}>FY{r.fiscal_year}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item}>
                      <td className="item">{itemLabel(item)}</td>
                      {rows.map((r) => (
                        <td key={r.fiscal_year}>{fmtMoney(r[stmt][item])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        );
      })}
    </div>
  );
}

export function HistoricalsTable({ periods }: { periods: HistoryPeriod[] }) {
  if (periods.length === 0) return null;
  const anyRestated = periods.some((p) =>
    STATEMENTS.some(([s]) =>
      Object.values(p[s]).some((f) => f.restated)),
  );
  return (
    <div className="stmt-block">
      {STATEMENTS.map(([stmt, label]) => {
        const items = [...new Set(periods.flatMap((p) => Object.keys(p[stmt])))];
        return (
          <details key={stmt} className="stmt">
            <summary>
              <span className="kicker">Historical {label.toLowerCase()}</span>
              <span className="count">
                FY{periods[0].fiscal_year}–FY
                {periods[periods.length - 1].fiscal_year} as filed
              </span>
            </summary>
            <div className="stmt-tablewrap">
              <table>
                <thead>
                  <tr>
                    <th className="item">$ B</th>
                    {periods.map((p) => (
                      <th key={p.fiscal_year}>
                        FY{p.fiscal_year}
                        {p.is_53_week ? " (53w)" : ""}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item}>
                      <td className="item">{itemLabel(item)}</td>
                      {periods.map((p) => {
                        const f = p[stmt][item];
                        return (
                          <td key={p.fiscal_year}>
                            {f ? fmtMoney(f.value) : "—"}
                            {f?.restated ? "†" : ""}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        );
      })}
      {anyRestated && (
        <div className="stmt-foot">† restated in a later filing — the
          restated value is used (latest-filed wins)</div>
      )}
    </div>
  );
}
