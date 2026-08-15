/* The terminal-growth slider (owner spec, redesign 2026-08-15).
   The interaction that is the thesis of the tool: drag from the model's
   assumption toward the market's and watch the valuation cross the price.

   No valuation math here, absolutely: the thumb SNAPS to engine-computed
   curve points and displays that point's exact per-share value — nothing is
   interpolated. The authoritative recompute fires on release (onCommit).
   Landmarks are exact curve points, inserted server-side. The track is
   draggable to the engine's hard block (WACC − 25bp); crossing the 10Y is
   warned, not blocked (owner ruling), shown as the hatched zone. */

import { useMemo, useRef, useState } from "react";
import { fmtPrice, fmtSignedPct } from "../format";
import type { Curve } from "../types";

function nearestIndex(points: [number, number | null][], x: number): number {
  let best = 0;
  let bestDist = Infinity;
  for (let i = 0; i < points.length; i++) {
    const d = Math.abs(points[i][0] - x);
    if (d < bestDist) {
      best = i;
      bestDist = d;
    }
  }
  return best;
}

export function GrowthSlider({
  curve,
  price,
  onCommit,
}: {
  curve: Curve;
  price: number;
  onCommit: (g: number) => void;
}) {
  const [lo, hi] = curve.domain;
  const pts = curve.points;
  const restIdx = useMemo(
    () => nearestIndex(pts, curve.landmarks.current),
    [pts, curve.landmarks.current],
  );
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const trackRef = useRef<HTMLDivElement>(null);

  const idx = dragIdx ?? restIdx;
  const [g, v] = pts[idx];
  const pos = (x: number) => ((x - lo) / (hi - lo)) * 100;

  const indexFromPointer = (clientX: number): number => {
    const el = trackRef.current;
    if (!el) return idx;
    const rect = el.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    return nearestIndex(pts, lo + frac * (hi - lo));
  };

  const commit = (i: number) => {
    setDragIdx(null);
    if (pts[i][0] !== curve.landmarks.current) onCommit(pts[i][0]);
  };

  const lm = curve.landmarks;
  const vsPrice = v != null ? v / price - 1 : null;

  return (
    <div className="sliderband">
      <div className="slider-head">
        <span className="kicker">
          Long-run growth (terminal g) — drag it, watch the value
        </span>
        <span className="slider-read">
          at {(g * 100).toFixed(2)}% →{" "}
          <b>{v != null ? fmtPrice(v) : "—"}</b>{" "}
          {vsPrice != null && (
            <span className={vsPrice < 0 ? "dn" : "up"}>
              {fmtSignedPct(vsPrice)}
            </span>
          )}{" "}
          vs price
        </span>
      </div>
      <div className="track-wrap">
        <div
          ref={trackRef}
          className="track"
          role="slider"
          tabIndex={0}
          aria-label="Long-run growth (terminal g)"
          aria-valuemin={lo * 100}
          aria-valuemax={hi * 100}
          aria-valuenow={g * 100}
          aria-valuetext={`${(g * 100).toFixed(2)}% → ${v != null ? fmtPrice(v) : "unavailable"} per share`}
          onPointerDown={(e) => {
            (e.target as HTMLElement).setPointerCapture(e.pointerId);
            setDragIdx(indexFromPointer(e.clientX));
          }}
          onPointerMove={(e) => {
            if (dragIdx != null) setDragIdx(indexFromPointer(e.clientX));
          }}
          onPointerUp={() => {
            if (dragIdx != null) commit(dragIdx);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft" && idx > 0) commit(idx - 1);
            if (e.key === "ArrowRight" && idx < pts.length - 1) commit(idx + 1);
          }}
        >
          {lm.rf != null && (
            <div
              className="warnzone"
              style={{ left: `${pos(lm.rf)}%`, right: 0 }}
            />
          )}
          <div className="fill" style={{ width: `${pos(g)}%` }} />
        </div>
        <span className="thumb" style={{ left: `${pos(g)}%` }} />
        <span className="lm model" style={{ left: `${pos(lm.derived)}%` }}>
          model <span className="g">{(lm.derived * 100).toFixed(2)}%</span>
        </span>
        {lm.rf != null && (
          <span className="lm ceiling" style={{ left: `${pos(lm.rf)}%` }}>
            10Y <span className="g">{(lm.rf * 100).toFixed(2)}%</span> — warned
            past here
          </span>
        )}
        {lm.market_implied != null && (
          <span
            className="lm market"
            style={{ left: `${pos(lm.market_implied)}%` }}
          >
            <span className="dot" />
            market{" "}
            <span className="g">{(lm.market_implied * 100).toFixed(2)}%</span>
          </span>
        )}
        <span className="lm floor">
          <span className="g">{(lo * 100).toFixed(1)}%</span>
        </span>
        <span className="lm stop">
          must stay under WACC{" "}
          <span className="g">{((hi + 0.0025) * 100).toFixed(2)}%</span>
        </span>
      </div>
      <div className="slider-cap">
        values read from a {pts.length}-point engine-computed curve during the
        drag; the model recomputes on release — the browser calculates nothing
      </div>
    </div>
  );
}
