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
