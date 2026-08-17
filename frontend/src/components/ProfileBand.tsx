/* Company profile — disclosed and reassignable (owner spec, 2026-08-15).
   The classification, the measured values that triggered it, and any
   user reassignment all render here; the rules live on the methodology
   surface. Reassigning re-derives the profile-owned defaults server-side —
   nothing is computed in the browser. */

import { fmtPct } from "../format";
import type { ProfileInfo } from "../types";

const PRIMARIES = ["compounder", "mature", "declining"] as const;
const MODIFIERS = [
  ["cyclical", "cyclical"],
  ["reinvestment_heavy", "reinvest-heavy"],
] as const;

function measuresText(p: ProfileInfo): string {
  const m = p.measures;
  const parts = [
    `revenue CAGR ${fmtPct(m.cagr, 1)}`,
    `latest yr ${fmtPct(m.g_latest, 1)}`,
  ];
  if (m.roic_median != null)
    parts.push(
      `ROIC ${fmtPct(m.roic_median, 1)} vs WACC ${fmtPct(m.wacc, 1)} ` +
        `(${m.roic_years_above_wacc}/${m.roic_years} yrs above)`,
    );
  parts.push(`margin range ${fmtPct(m.margin_range, 1)}`);
  if (m.capex_da != null)
    parts.push(`capex/depreciation ${m.capex_da.toFixed(2)}×`);
  return parts.join(" · ");
}

export function ProfileBand({
  profile,
  requested,
  onReassign,
}: {
  profile: ProfileInfo;
  requested: string | null; // the user's reassignment tag, null = auto
  onReassign: (tag: string | null) => void;
}) {
  const tagOf = (primary: string, mods: string[]) =>
    [primary, ...mods].join("+");

  const setPrimary = (primary: string) =>
    onReassign(tagOf(primary, profile.modifiers));
  const toggleModifier = (mod: string) => {
    const mods = profile.modifiers.includes(mod)
      ? profile.modifiers.filter((x) => x !== mod)
      : [...profile.modifiers, mod];
    onReassign(tagOf(profile.primary, mods));
  };

  return (
    <div className="profile-band">
      <div className="profile-id">
        <span className="kicker">Company profile</span>
        <span className="profile-tag">
          {profile.tag.replace(/_/g, " ").replace(/\+/g, " + ")}
        </span>
        {profile.reassigned && (
          <span className="profile-reassigned">
            reassigned by you · auto:{" "}
            {profile.auto_tag.replace(/_/g, " ").replace(/\+/g, " + ")}
          </span>
        )}
      </div>
      <div className="profile-meta">
        <span className="measured">
          measured from its own filings: {measuresText(profile)}
        </span>
        {profile.notes.map((n, i) => (
          <span key={i} className="profile-note">
            {n}
          </span>
        ))}
      </div>
      <div className="profile-options" role="group" aria-label="Reassign profile">
        {PRIMARIES.map((p) => (
          <button
            key={p}
            type="button"
            className={`popt${profile.primary === p ? " on" : ""}`}
            aria-pressed={profile.primary === p}
            onClick={() => {
              if (profile.primary !== p) setPrimary(p);
            }}
          >
            <span className="popt-head">
              <span className="preset-glyph">
                {profile.primary === p ? "●" : "○"}
              </span>
              <span className="popt-name">{p}</span>
            </span>
            <span className="popt-why">{profile.blurbs?.[p] ?? ""}</span>
          </button>
        ))}
      </div>
      <div className="profile-controls" role="group" aria-label="Modifiers">
        {MODIFIERS.map(([mod, label]) => (
          <button
            key={mod}
            type="button"
            className={`pseg mod${profile.modifiers.includes(mod) ? " on" : ""}`}
            aria-pressed={profile.modifiers.includes(mod)}
            title={profile.blurbs?.[mod] ?? ""}
            onClick={() => toggleModifier(mod)}
          >
            +{label}
          </button>
        ))}
        {profile.modifiers.map((mod) => (
          <span key={mod} className="mod-why">
            {profile.blurbs?.[mod] ?? ""}
          </span>
        ))}
        {requested != null && (
          <button
            type="button"
            className="pseg reset"
            onClick={() => onReassign(null)}
          >
            back to auto ({profile.auto_tag.replace(/_/g, " ")})
          </button>
        )}
      </div>
      <div className="profile-framing">{profile.framing}</div>
    </div>
  );
}
