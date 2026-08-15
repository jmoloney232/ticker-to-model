/* The owner-named component tests: default / override / reset logic, plus
   unit parsing back to engine-native values. */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { row } from "../test-fixtures";
import { Assumptions } from "./Assumptions";

afterEach(cleanup);

function mount(rows = [row()], over: Record<string, unknown> = {}) {
  const onOverride = vi.fn();
  const onResetAll = vi.fn();
  render(
    <Assumptions
      rows={rows}
      overrideCount={0}
      overrideError={null}
      onOverride={onOverride}
      onResetAll={onResetAll}
      {...over}
    />,
  );
  return { onOverride, onResetAll };
}

describe("assumption rows", () => {
  it("shows the derived value in display units with its glyph", () => {
    mount();
    expect(
      (screen.getByLabelText("Revenue growth fy1") as HTMLInputElement).value,
    ).toBe("16.12");
    expect(screen.getByText("derived")).toBeTruthy();
  });

  it("commits an edit converted back to engine-native units", () => {
    const { onOverride } = mount();
    const input = screen.getByLabelText("Revenue growth fy1");
    fireEvent.change(input, { target: { value: "20" } });
    fireEvent.blur(input);
    expect(onOverride).toHaveBeenCalledWith("revenue_growth_fy1", 0.2);
  });

  it("does not emit an override when the value is unchanged", () => {
    const { onOverride } = mount();
    const input = screen.getByLabelText("Revenue growth fy1");
    fireEvent.change(input, { target: { value: "16.12" } });
    fireEvent.blur(input);
    expect(onOverride).not.toHaveBeenCalled();
  });

  it("reverts a non-numeric edit without emitting", () => {
    const { onOverride } = mount();
    const input = screen.getByLabelText("Revenue growth fy1");
    fireEvent.change(input, { target: { value: "abc" } });
    fireEvent.blur(input);
    expect(onOverride).not.toHaveBeenCalled();
  });

  it("marks a user-overridden row and resets it per field", () => {
    const { onOverride } = mount([
      row({ provenance: "user", value: 0.2, derived_default: 0.1612 }),
    ]);
    expect(screen.getByText("you")).toBeTruthy();
    fireEvent.click(
      screen.getByLabelText("Reset Revenue growth fy1 to its derived default"),
    );
    expect(onOverride).toHaveBeenCalledWith("revenue_growth_fy1", null);
  });

  it("toggles a flag field", () => {
    const { onOverride } = mount([
      row({ name: "sbc_addback", label: "Sbc addback", unit: "flag", value: false }),
    ]);
    fireEvent.click(screen.getByText("no"));
    expect(onOverride).toHaveBeenCalledWith("sbc_addback", true);
  });

  it("renders an underivable (null) value as locked em-dash", () => {
    mount([row({ name: "exit_multiple", label: "Exit multiple", unit: "x", value: null })]);
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.queryByLabelText("Exit multiple")).toBeNull();
  });

  it("offers reset-all only when overrides exist", () => {
    const { onResetAll } = mount([row()], { overrideCount: 2 });
    fireEvent.click(screen.getByText("reset all 2 edits"));
    expect(onResetAll).toHaveBeenCalled();
  });

  it("never drops an unrecognized field — it lands under Other", () => {
    mount([row({ name: "mystery_new_field", label: "Mystery new field" })]);
    expect(screen.getByText("Other")).toBeTruthy();
    expect(screen.getByText("Mystery new field")).toBeTruthy();
  });

  it("prints the hovered row's rule in the inspector strip", () => {
    mount();
    fireEvent.mouseEnter(screen.getByText("Revenue growth fy1"));
    expect(screen.getByText("3y revenue CAGR, capped at 30%")).toBeTruthy();
  });
});

describe("discrete horizon control (forecast_years)", () => {
  const horizon = (value = 5) =>
    row({
      name: "forecast_years",
      label: "Forecast years",
      unit: "years",
      value,
      derived_default: 5,
    });

  it("renders 5/7/10 as buttons — no free-text input to 400 on", () => {
    mount([horizon()]);
    expect(screen.queryByRole("textbox")).toBeNull();
    for (const opt of ["5", "7", "10"]) {
      expect(screen.getByRole("button", { name: `Forecast years: ${opt} years` })).toBeTruthy();
    }
  });

  it("marks the current horizon and overrides on picking another", () => {
    const { onOverride } = mount([horizon()]);
    const five = screen.getByRole("button", { name: "Forecast years: 5 years" });
    expect(five.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(five);
    expect(onOverride).not.toHaveBeenCalled(); // re-picking the value is a no-op
    fireEvent.click(screen.getByRole("button", { name: "Forecast years: 7 years" }));
    expect(onOverride).toHaveBeenCalledWith("forecast_years", 7);
  });

  it("displays an edited horizon as an integer with the years unit", () => {
    mount([horizon(7)]);
    const seven = screen.getByRole("button", { name: "Forecast years: 7 years" });
    expect(seven.getAttribute("aria-pressed")).toBe("true");
    expect(seven.textContent).toBe("7"); // integer, never "7.00"
    expect(screen.getByText("years")).toBeTruthy();
  });
});

describe("new engine fields", () => {
  it("renders terminal_roic_fade as a boolean toggle", () => {
    const { onOverride } = mount([
      row({
        name: "terminal_roic_fade",
        label: "Terminal roic fade",
        unit: "flag",
        value: false,
        derived_default: false,
      }),
    ]);
    fireEvent.click(screen.getByText("no"));
    expect(onOverride).toHaveBeenCalledWith("terminal_roic_fade", true);
  });

  it("renders the rf ceiling as a read-only reference row, not an input", () => {
    mount([
      row({
        name: "terminal_growth_rf_ceiling",
        label: "Terminal growth rf ceiling",
        unit: "rate",
        value: 0.0468,
        editable: false,
      }),
    ]);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByText("4.68")).toBeTruthy();
    expect(screen.getByText("computed")).toBeTruthy();
  });
});

describe("preset source notes", () => {
  const NOTE = "Damodaran implied US equity risk premium, January 2026";
  const erp = row({
    name: "erp",
    label: "Erp",
    value: 0.0423,
    provenance: "preset:damodaran_implied",
    rule: `${NOTE} — historical-average ERP, editable`,
  });

  it("prints the note beside the rule, deduplicating the serializer prefix", () => {
    mount([erp], { presetNotes: { erp: NOTE } });
    fireEvent.mouseEnter(screen.getByText("Erp"));
    expect(screen.getByText(NOTE)).toBeTruthy();
    expect(screen.getByText("source")).toBeTruthy();
    // the rule text reads once, without the note baked in front of it
    expect(screen.getByText("historical-average ERP, editable")).toBeTruthy();
  });

  it("shows no source line for non-preset rows", () => {
    mount([row()], { presetNotes: { erp: NOTE } });
    fireEvent.mouseEnter(screen.getByText("Revenue growth fy1"));
    expect(screen.queryByText("source")).toBeNull();
  });
});
