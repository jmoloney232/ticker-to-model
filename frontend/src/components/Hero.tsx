/* The 1c hero: reversed steel field. Left — assumed vs market-implied
   perpetual growth at 52px. Right — every registry method against the
   market price. The method list, labels, and order come from the server;
   the only client arithmetic is pixel scaling and the gap label (a
   display subtraction). */

import { fmtPrice, fmtSignedPct } from "../format";
import type { MethodOut, ModelOk } from "../types";
import { findMethod } from "../types";

const CHART_MAX_PX = 120; // ds: mockup bar geometry inside the 200px chart

function solveStatusText(status: string): string {
  if (status === "no_solution_below_wacc")
    return "no growth below your WACC reaches the price";
  return "no solution in the searched range";
}

/* Bars and captions live in separate grid rows so the market reference
   line (positioned from the plot's bottom edge) shares the bars' exact
   baseline — captions can never push the bars off the line's scale. */

function MethodBar({ mo, pxPerUnit }: { mo: MethodOut; pxPerUnit: number }) {
  if (!mo.available) {
    return (
      <div className="leg-off">
        <div className="kicker">{mo.label} — unavailable</div>
        {mo.reason.message}
      </div>
    );
  }
  return (
    <div>
      <div className="bar-fig">{fmtPrice(mo.value_per_share)}</div>
      <div
        className="bar"
        style={{ height: Math.max(4, mo.value_per_share * pxPerUnit) }}
      />
    </div>
  );
}

function MethodCap({ mo }: { mo: MethodOut }) {
  if (!mo.available) return <div />;
  const dn = mo.vs_price < 0;
  return (
    <div className="bar-cap">
      {mo.label}
      <br />
      <span className={`delta ${dn ? "dn" : "up"}`}>
        {fmtSignedPct(mo.vs_price)}
      </span>
    </div>
  );
}

export function Hero({ model }: { model: ModelOk }) {
  const price = model.market.price.value;
  const methods = model.valuation; // server-ordered registry
  const gordon = findMethod(methods, "gordon");
  const exit = findMethod(methods, "exit_multiple");
  const solve = model.reverse?.terminal_growth ?? null;
  const statedG = model.assumptions.find(
    (a) => a.name === "terminal_growth",
  )?.value as number | undefined;
  const impliedG = solve?.implied ?? null;
  const waccPct = ((model.wacc.wacc as number) * 100).toFixed(2);

  const shares = model.assumptions.find((a) => a.name === "share_count")
    ?.value as number | undefined;

  const values = [
    price,
    ...methods.filter((mo) => mo.available).map((mo) => mo.value_per_share),
  ];
  const pxPerUnit = CHART_MAX_PX / Math.max(...values);
  const mktPx = price * pxPerUnit;
  /* the market bar sits after the first method (the 1c straddle look);
     every other column is a registry method in server order */
  const cols = methods.length + 1;

  const sentence = (() => {
    if (impliedG == null && solve != null)
      return `At your ${waccPct}% WACC, ${solveStatusText(solve.status)}.`;
    if (impliedG == null) return null;
    const parts = [
      `Holding your ${waccPct}% WACC, the perpetuity model needs ` +
        `${(impliedG * 100).toFixed(2)}% forever to reach the market price.`,
    ];
    if (gordon?.available && exit?.available) {
      const straddles = gordon.vs_price < 0 !== exit.vs_price < 0;
      parts.push(
        straddles
          ? "The two DCF methods straddle the price."
          : gordon.vs_price < 0
            ? "Both DCF methods sit below the price."
            : "Both DCF methods sit above the price.",
      );
    }
    return parts.join(" ");
  })();

  const marketBar = (
    <div key="__market">
      <div className="bar-fig mkt">{fmtPrice(price)}</div>
      <div className="bar mkt" style={{ height: mktPx }} />
    </div>
  );
  const marketCap = (
    <div className="bar-cap" key="__market">
      market price
      <br />
      <span className="delta">reference</span>
    </div>
  );
  const bars = methods.map((mo) => (
    <MethodBar key={mo.id} mo={mo} pxPerUnit={pxPerUnit} />
  ));
  const caps = methods.map((mo) => <MethodCap key={mo.id} mo={mo} />);
  bars.splice(1, 0, marketBar);
  caps.splice(1, 0, marketCap);

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
            Value per share — every method against the market
          </span>
          <span className="straddle-note">
            USD{shares != null ? ` · ${(shares / 1e9).toFixed(2)}B shares` : ""}
          </span>
        </div>
        <div className="chart">
          <div
            className="plot"
            style={{
              gridTemplateColumns: `repeat(${cols}, 1fr)`,
              columnGap: cols > 3 ? 36 : undefined,
            }}
          >
            <div className="mkt-line" style={{ bottom: mktPx }} />
            {bars}
          </div>
          <div
            className="caps"
            style={{
              gridTemplateColumns: `repeat(${cols}, 1fr)`,
              columnGap: cols > 3 ? 36 : undefined,
            }}
          >
            {caps}
          </div>
        </div>
        {sentence && <div className="straddle-sentence">{sentence}</div>}
      </div>
    </div>
  );
}
