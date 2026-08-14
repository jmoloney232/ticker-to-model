/* Designed degraded states — refusals and outages are first-class content,
   never error styling (non-negotiable #4 / owner status rules). */

import type { ReactNode } from "react";
import { navigate } from "../App";
import type { Reason } from "../types";

export function StateCard({
  kicker,
  title,
  message,
  detail,
  children,
}: {
  kicker: string;
  title: string;
  message: string;
  detail?: string | null;
  children?: ReactNode;
}) {
  return (
    <div className="statecard">
      <span className="regmark tl" />
      <span className="regmark br" />
      <div className="kicker">{kicker}</div>
      <h2>{title}</h2>
      <p className="msg">{message}</p>
      {detail && <div className="detail">{detail}</div>}
      <div className="row">
        {children}
        <a
          href="/"
          onClick={(e) => {
            e.preventDefault();
            navigate("/");
          }}
        >
          ← Another ticker
        </a>
      </div>
    </div>
  );
}

export function reasonDetail(reason: Reason): string | null {
  const entries = Object.entries(reason.detail ?? {});
  if (entries.length === 0) return null;
  return entries.map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("\n");
}
