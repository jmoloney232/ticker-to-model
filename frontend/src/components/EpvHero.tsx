/* The EPV view's hero, on the same reversed steel field as the DCF's.
   Left — the number being capitalized: the normalized EBIT margin, its
   per-profile rule, and its provenance. Right — the EPV family's methods
   against the market price. Every number is API output; the only client
   arithmetic is pixel scaling. */

import { fmtPct, fmtPrice, fmtSignedPct } from "../format";
import type { MethodOut, ModelOk } from "../types";
import { methodDetail } from "../types";

const CHART_MAX_PX = 120; // ds: mockup bar geometry (shared with Hero)

function Bar({ mo, pxPerUnit }: { mo: MethodOut; pxPerUnit: number }) {
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

export function EpvHero({ model }: { model: ModelOk }) {
  const price = model.market.price.value;
  const methods = model.valuation.filter((mo) => mo.family === "epv");
  const marginRow = model.assumptions.find((a) => a.name === "epv_margin");
  const wacc = model.wacc.wacc as number;

  const values = [
    price,
    ...methods.filter((mo) => mo.available).map((mo) => mo.value_per_share),
  ];
  const pxPerUnit = CHART_MAX_PX / Math.max(...values);
  const mktPx = price * pxPerUnit;

  return (
    <div className="hero">
      <div className="gap-pane">
        <div className="kicker">The number being capitalized</div>
        <div className="gap-block">
          <div className="gap-fig">
            {marginRow != null
              ? ((marginRow.value as number) * 100).toFixed(1)
              : "—"}
            <span className="u">%</span>
          </div>
          <div className="gap-cap">
            normalized operating margin
            {marginRow?.provenance === "user" ? " — edited by you" : ""}
          </div>
        </div>
        <div className="epv-rule">{marginRow?.rule}</div>
        <div className="gap-cap">
          taxed at the marginal rate, then capitalized at the{" "}
          {fmtPct(wacc)} WACC the DCF view also uses — no growth assumed,
          none priced.
        </div>
      </div>

      <div className="straddle">
        <div className="straddle-head">
          <span className="kicker">
            Value per share — earnings power against the market
          </span>
          <span className="straddle-note">USD</span>
        </div>
        <div className="chart">
          <div
            className="plot"
            style={{ gridTemplateColumns: `repeat(${methods.length + 1}, 1fr)` }}
          >
            <div className="mkt-line" style={{ bottom: mktPx }} />
            {methods.map((mo) => (
              <Bar key={mo.id} mo={mo} pxPerUnit={pxPerUnit} />
            ))}
            <div>
              <div className="bar-fig mkt">{fmtPrice(price)}</div>
              <div className="bar mkt" style={{ height: mktPx }} />
            </div>
          </div>
          <div
            className="caps"
            style={{ gridTemplateColumns: `repeat(${methods.length + 1}, 1fr)` }}
          >
            {methods.map((mo) =>
              mo.available ? (
                <div className="bar-cap" key={mo.id}>
                  {mo.label}
                  <br />
                  <span className={`delta ${mo.vs_price < 0 ? "dn" : "up"}`}>
                    {fmtSignedPct(mo.vs_price)}
                  </span>
                </div>
              ) : (
                <div key={mo.id} />
              ),
            )}
            <div className="bar-cap">
              market price
              <br />
              <span className="delta">reference</span>
            </div>
          </div>
        </div>
        {methodDetail(methods[0], "nopat_normalized") != null && (
          <div className="straddle-sentence">
            Normalized NOPAT of{" "}
            {fmtPrice(methodDetail(methods[0], "nopat_normalized")! / 1e9)}B a
            year, held flat forever — maintenance capex equals depreciation,
            working capital stays put.
          </div>
        )}
      </div>
    </div>
  );
}
