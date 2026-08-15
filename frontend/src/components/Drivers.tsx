/* The five headline drivers, ranked server-side by actual impact for THIS
   company (methodology: driver_ranking). Direction cues tell the reader
   which way the lever moves the valuation; the bar scales to the top
   driver. All numbers arrive computed — nothing is derived here. */

import type { Driver } from "../types";

const BAR_MAX_PX = 120; // ds: mockup driver-bar geometry

function impactText(d: Driver): string {
  const dollars =
    d.impact_per_share >= 20
      ? `$${Math.round(d.impact_per_share)}`
      : `$${d.impact_per_share.toFixed(2)}`;
  return `${d.step_label} ⇒ ${d.direction === "down" ? "∓" : "±"}${dollars}/sh`;
}

export function Drivers({
  drivers,
  company,
}: {
  drivers: Driver[];
  company: string;
}) {
  if (drivers.length === 0) return null;
  const top = drivers[0].impact_per_share || 1;
  return (
    <div className="drivers">
      <div className="kicker">What moves this valuation — ranked for {company}</div>
      {drivers.map((d) => (
        <div className="driver" key={d.name}>
          <span className="name">{d.label}</span>
          <span className="dir">
            <span className="a">
              {d.direction === "down" ? "↑ input → ↓ value" : "↑ input → ↑ value"}
            </span>
            {d.note ? ` · ${d.note}` : ""}
          </span>
          <span className="impact">
            <span
              className="bar-i"
              style={{
                width: Math.max(
                  3,
                  (d.impact_per_share / top) * BAR_MAX_PX,
                ),
              }}
            />
            <span className="v">{impactText(d)}</span>
          </span>
        </div>
      ))}
      <div className="note">
        impact = per-share move for the standard step shown, computed by the
        engine for this company — edit any of these in Model
      </div>
    </div>
  );
}
