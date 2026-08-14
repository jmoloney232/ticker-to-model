/* The 1c hero: reversed steel field. Left — assumed vs market-implied
   perpetual growth at 52px. Right — both terminal methods straddling the
   market price. Every number is API output; the only client arithmetic is
   pixel scaling and the gap label (a display subtraction). */

import { fmtPrice, fmtSignedPct } from "../format";
import type { Leg, ModelOk } from "../types";

const CHART_MAX_PX = 120; // ds: mockup bar geometry inside the 200px chart

function solveStatusText(status: string): string {
  if (status === "no_solution_below_wacc")
    return "no growth below your WACC reaches the price";
  return "no solution in the searched range";
}

function LegColumn({
  leg,
  label,
  pxPerUnit,
}: {
  leg: Leg;
  label: string;
  pxPerUnit: number;
}) {
  if (!leg.available) {
    return (
      <div className="leg-off">
        <div className="kicker">{label} — unavailable</div>
        {leg.reason.message}
      </div>
    );
  }
  const dn = leg.vs_price < 0;
  return (
    <div>
      <div className="bar-fig">{fmtPrice(leg.value_per_share)}</div>
      <div
        className="bar"
        style={{ height: Math.max(4, leg.value_per_share * pxPerUnit) }}
      />
      <div className="bar-cap">
        {label}
        <br />
        <span className={`delta ${dn ? "dn" : "up"}`}>
          {fmtSignedPct(leg.vs_price)}
        </span>
      </div>
    </div>
  );
}

export function Hero({ model }: { model: ModelOk }) {
  const price = model.market.price.value;
  const gordon = model.valuation.gordon;
  const exit = model.valuation.exit_multiple;
  const solve = model.reverse?.terminal_growth ?? null;
  const statedG = model.assumptions.find(
    (a) => a.name === "terminal_growth",
  )?.value as number | undefined;
  const impliedG = solve?.implied ?? null;
  const waccPct = ((model.wacc.wacc as number) * 100).toFixed(2);

  const exitMult = model.assumptions.find((a) => a.name === "exit_multiple")
    ?.value as number | undefined;
  const shares = model.assumptions.find((a) => a.name === "share_count")
    ?.value as number | undefined;

  const values = [price];
  if (gordon.available) values.push(gordon.value_per_share);
  if (exit.available) values.push(exit.value_per_share);
  const pxPerUnit = CHART_MAX_PX / Math.max(...values);
  const mktPx = price * pxPerUnit;

  const sentence = (() => {
    if (impliedG == null && solve != null)
      return `At your ${waccPct}% WACC, ${solveStatusText(solve.status)}.`;
    if (impliedG == null) return null;
    const parts = [
      `Holding your ${waccPct}% WACC, the perpetuity model needs ` +
        `${(impliedG * 100).toFixed(2)}% forever to reach the market price.`,
    ];
    if (gordon.available && exit.available) {
      const straddles = gordon.vs_price < 0 !== exit.vs_price < 0;
      parts.push(
        straddles
          ? "The two methods straddle the price."
          : gordon.vs_price < 0
            ? "Both methods sit below the price."
            : "Both methods sit above the price.",
      );
    }
    return parts.join(" ");
  })();

  return (
    <div className="hero">
      <div className="gap-pane">
        <div className="kicker">The gap that matters</div>
        <div className="gap-block">
          <div className="gap-fig">
            {statedG != null ? (statedG * 100).toFixed(2) : "—"}
            <span className="u">%</span>
          </div>
          <div className="gap-cap">perpetual growth you assume</div>
        </div>
        <div className="gap-join">
          <span className="rule" />
          <span className="pp">
            {impliedG != null && statedG != null
              ? `gap ${((impliedG - statedG) * 100).toFixed(2)} pp`
              : "gap —"}
          </span>
          <span className="rule" />
        </div>
        {impliedG != null ? (
          <div>
            <div className="gap-fig implied">
              {(impliedG * 100).toFixed(2)}
              <span className="u">%</span>
            </div>
            <div className="gap-cap">
              perpetual growth the market is pricing at {fmtPrice(price)}
            </div>
          </div>
        ) : (
          <div className="gap-none">
            {solve ? solveStatusText(solve.status) : "reverse solve unavailable"}
          </div>
        )}
      </div>

      <div className="straddle">
        <div className="straddle-head">
          <span className="kicker">
            Value per share — both terminal methods against the market
          </span>
          <span className="straddle-note">
            USD{shares != null ? ` · ${(shares / 1e9).toFixed(2)}B shares` : ""}
          </span>
        </div>
        <div className="chart">
          <div className="mkt-line" style={{ bottom: mktPx }} />
          <div className="mkt-tag" style={{ bottom: mktPx + 4 }}>
            market {fmtPrice(price)}
          </div>
          <LegColumn leg={gordon} label="perpetuity growth" pxPerUnit={pxPerUnit} />
          <div>
            <div className="bar-fig mkt">{fmtPrice(price)}</div>
            <div className="bar mkt" style={{ height: mktPx }} />
            <div className="bar-cap">
              market price
              <br />
              <span className="delta">reference</span>
            </div>
          </div>
          <LegColumn
            leg={exit}
            label={
              exitMult != null
                ? `${exitMult.toFixed(1)}× exit multiple`
                : "exit multiple"
            }
            pxPerUnit={pxPerUnit}
          />
        </div>
        {sentence && <div className="straddle-sentence">{sentence}</div>}
      </div>
    </div>
  );
}
