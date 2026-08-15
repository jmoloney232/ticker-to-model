/* Sensitivity pane per 1c: heat grids (WACC × g, WACC × multiple), the stat
   block, and the market-implied solves. Heat normalization and the
   cells-reaching-price count are display aggregation of API cells; the cells
   themselves are engine output. */

import { Fragment } from "react";
import { fmtPct, fmtPrice } from "../format";
import type { Grid, ImpliedSolve, ModelOk } from "../types";
import { findMethod, methodDetail } from "../types";

const HEAT_BASE = 0.04; // ds: mockup heat ramp
const HEAT_SPAN = 0.3;
const HEAT_FLIP = 0.78;

function HeatGrid({
  grid,
  isMultiple,
  price,
}: {
  grid: Grid;
  isMultiple: boolean;
  price: number;
}) {
  const flat = grid.cells.flat().filter((c): c is number => c != null);
  const lo = Math.min(...flat);
  const hi = Math.max(...flat);
  const baseR = grid.rows.length >> 1;
  const baseC = grid.cols.length >> 1;
  const fmtCol = (c: number) =>
    isMultiple ? c.toFixed(1) : (c * 100).toFixed(2);
  return (
    <div>
      <div
        className="sgrid"
        style={{
          gridTemplateColumns: `52px repeat(${grid.cols.length}, 1fr)`, // ds: mockup grid spec
        }}
      >
        <span className="corner">WACC</span>
        {grid.cols.map((c, j) => (
          <span key={j} className="chead">
            {fmtCol(c)}
          </span>
        ))}
        {grid.rows.map((w, i) => (
          <Fragment key={i}>
            <span className={`rhead${i === baseR ? " base" : ""}`}>
              {(w * 100).toFixed(2)}
            </span>
            {grid.cells[i].map((v, j) => {
              if (v == null)
                return (
                  <span key={j} className="cell" style={{ color: "var(--text-4)" }}>
                    —
                  </span>
                );
              const t = hi > lo ? (v - lo) / (hi - lo) : 0;
              return (
                <span
                  key={j}
                  className={`cell${i === baseR && j === baseC ? " base" : ""}`}
                  style={{
                    // ds: heat ramp — steel at a computed alpha (mockup formula)
                    background: `rgba(89, 128, 166, ${(HEAT_BASE + t * HEAT_SPAN).toFixed(3)})`,
                    color:
                      t > HEAT_FLIP ? "var(--steel-night)" : "var(--ink)",
                  }}
                >
                  {v.toFixed(0)}
                </span>
              );
            })}
          </Fragment>
        ))}
      </div>
      <div className="saxis">
        {isMultiple ? "exit EV/EBITDA (×) →" : "terminal growth (%) →"}
      </div>
      {flat.every((v) => v < price) && (
        <div className="gridoff">
          No cell in this range reaches {fmtPrice(price)}.
        </div>
      )}
    </div>
  );
}

const SOLVE_LABELS: Record<string, string> = {
  terminal_growth: "Terminal growth",
  revenue_growth_fy1: "Revenue growth, FY1",
  ebitda_margin: "EBITDA margin",
  capex_pct: "Capex, % revenue",
};

function fmtSolve(field: string, v: number | null): string {
  if (v == null) return "—";
  return field === "capex_pct" ||
    field === "ebitda_margin" ||
    field.includes("growth")
    ? `${(v * 100).toFixed(2)}%`
    : v.toFixed(2);
}

function Solves({ solves }: { solves: Record<string, ImpliedSolve> }) {
  return (
    <div className="solves">
      <div className="kicker">Market-implied — solved from the price</div>
      {Object.entries(solves).map(([field, s]) => (
        <div key={field} className="solve">
          <span className="k">{SOLVE_LABELS[field] ?? field}</span>
          <span className="v">
            {fmtSolve(field, s.derived)} →{" "}
            {s.implied != null ? (
              <span className="imp">{fmtSolve(field, s.implied)}</span>
            ) : (
              <span className="none">
                {s.status === "no_solution_below_wacc"
                  ? "none below WACC"
                  : "none in range"}
              </span>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}

export function Sensitivity({ model }: { model: ModelOk }) {
  const price = model.market.price.value;
  const g = model.sensitivity["wacc_x_g"];
  const mult = model.sensitivity["wacc_x_multiple"];
  const gordon = findMethod(model.valuation, "gordon");
  const exit = findMethod(model.valuation, "exit_multiple");

  const allCells = [
    ...(g ? g.cells.flat() : []),
    ...(mult ? mult.cells.flat() : []),
  ].filter((c): c is number => c != null);
  const reaching = allCells.filter((c) => c >= price).length;

  const counts = model.provenance_counts;
  const total = counts.derived + counts.preset + counts.user;

  return (
    <div className="spane">
      <div className="paneband">
        <span className="kicker">Sensitivity · WACC × terminal growth</span>
        <span className="hint">steel = higher value</span>
      </div>
      <div className="spad">
        {g ? (
          <HeatGrid grid={g} isMultiple={false} price={price} />
        ) : (
          <div className="gridoff">
            WACC × g grid unavailable —{" "}
            {gordon && !gordon.available
              ? gordon.reason.message
              : "the Gordon leg is unavailable."}
          </div>
        )}
      </div>
      <div className="paneband">
        <span className="kicker">Sensitivity · WACC × exit multiple</span>
        <span className="hint">base projection, re-priced</span>
      </div>
      <div className="spad">
        {mult ? (
          <HeatGrid grid={mult} isMultiple price={price} />
        ) : (
          <div className="gridoff">
            WACC × multiple grid unavailable —{" "}
            {exit && !exit.available
              ? exit.reason.message
              : "the exit leg is unavailable."}
          </div>
        )}

        <div className="stats">
          <div className="stat">
            <span className="k">
              Cells in this range reaching {fmtPrice(price)}
            </span>
            <span className={`v${reaching === 0 ? " dn" : ""}`}>
              {reaching} of {allCells.length}
            </span>
          </div>
          <div className="stat">
            <span className="k">
              Terminal value share of EV — perpetuity / exit
            </span>
            <span className="v">
              {fmtPct(methodDetail(gordon, "tv_share_of_ev"), 0)} /{" "}
              {fmtPct(methodDetail(exit, "tv_share_of_ev"), 0)}
            </span>
          </div>
          <div className="stat">
            <span className="k">Fields left at their derived default</span>
            <span className="v">
              {counts.derived} of {total}
            </span>
          </div>
          {model.coverage && (
            <div className="stat">
              <span className="k">Balance sheet lines mapped — A / L</span>
              <span className="v">
                {fmtPct(model.coverage.assets_named_share, 0)} /{" "}
                {fmtPct(model.coverage.liabilities_named_share, 0)}
              </span>
            </div>
          )}
        </div>

        {model.reverse && <Solves solves={model.reverse} />}
      </div>
    </div>
  );
}
