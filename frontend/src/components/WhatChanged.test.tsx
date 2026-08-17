/* What-changed diff (Part 3, owner spec 2026-08-17): presentation-only diff
   of the server's assumption rows across a preset/profile switch. */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { diffAssumptions, WhatChanged } from "./WhatChanged";
import type { AssumptionRow } from "../types";

function row(
  name: string,
  value: number | boolean | null,
  unit = "rate",
): AssumptionRow {
  return {
    name,
    label: name.replace(/_/g, " "),
    value,
    unit,
    provenance: "derived",
    derived_default: value,
    rule: "",
    editable: true,
  };
}

describe("diffAssumptions", () => {
  it("reports moved rows with formatted from → to and skips unmoved ones", () => {
    const prev = [row("terminal_growth", 0.025), row("capex_pct", 0.08),
                  row("midyear", true, "flag")];
    const next = [row("terminal_growth", 0.0463), row("capex_pct", 0.08),
                  row("midyear", true, "flag")];
    const d = diffAssumptions(prev, next);
    expect(d).toHaveLength(1);
    expect(d[0].label).toBe("terminal growth");
    expect(d[0].from).toBe("2.50%");
    expect(d[0].to).toBe("4.63%");
  });

  it("treats float noise as unmoved and flag flips as moves", () => {
    const prev = [row("erp", 0.05), row("sbc_addback", false, "flag")];
    const next = [row("erp", 0.05 + 1e-15), row("sbc_addback", true, "flag")];
    const d = diffAssumptions(prev, next);
    expect(d.map((c) => c.name)).toEqual(["sbc_addback"]);
    expect(d[0].from).toBe("no");
    expect(d[0].to).toBe("yes");
  });
});

describe("WhatChanged", () => {
  it("names the cause and prints each move", () => {
    render(
      <WhatChanged
        cause="preset “Street convention”"
        rows={[{ name: "g", label: "Long-run growth", from: "2.50%", to: "4.63%" }]}
        onDismiss={() => {}}
      />,
    );
    expect(screen.getByText(/Street convention/)).toBeTruthy();
    expect(screen.getByText("2.50% → 4.63%")).toBeTruthy();
  });

  it("says so plainly when nothing moved", () => {
    render(<WhatChanged cause="x" rows={[]} onDismiss={() => {}} />);
    expect(screen.getByText(/No assumption moved/)).toBeTruthy();
  });
});
