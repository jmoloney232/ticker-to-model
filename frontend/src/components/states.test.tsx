/* Contract-state rendering: unavailable legs, folded/hard caveats, refusal
   cards — the states the owner requires to be designed, not accidental. */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { modelOk, warning } from "../test-fixtures";
import { Caveats } from "./Caveats";
import { Hero } from "./Hero";
import { StateCard } from "./StateCard";

afterEach(cleanup);

describe("hero legs", () => {
  it("renders an unavailable leg as a reasoned plate, never an error", () => {
    const m = modelOk();
    m.valuation.exit_multiple = {
      available: false,
      reason: {
        code: "exit_multiple_unavailable",
        message: "No exit multiple — FY0 EBITDA ≤ 0.",
        detail: {},
      },
    };
    render(<Hero model={m} />);
    expect(screen.getByText(/No exit multiple — FY0 EBITDA/)).toBeTruthy();
    expect(screen.getByText("280.99")).toBeTruthy();
  });

  it("shows the implied-growth gap when the solve exists", () => {
    render(<Hero model={modelOk()} />);
    expect(screen.getByText("gap 3.74 pp")).toBeTruthy();
    expect(screen.getAllByText(/6\.24/).length).toBeGreaterThan(0);
  });

  it("states the no-solution reverse solve honestly", () => {
    const m = modelOk();
    m.reverse = {
      terminal_growth: {
        derived: 0.025,
        implied: null,
        status: "no_solution_below_wacc",
        target_price: 25.33,
      },
    };
    render(<Hero model={m} />);
    expect(
      screen.getAllByText(/no growth below your WACC reaches the price/i).length,
    ).toBeGreaterThan(0);
  });
});

describe("caveats", () => {
  it("folds a repeated code into a disclosure without dropping any row", () => {
    const ws = [1, 2, 3, 4, 5].map((y) =>
      warning({ fiscal_year: 2020 + y, message: `FY${2020 + y}: x unmapped` }),
    );
    render(<Caveats warnings={ws} checks={[]} />);
    expect(screen.getByText(/Caveats — 5, model valid/)).toBeTruthy();
    expect(screen.getByText(/5× optional unmapped/)).toBeTruthy();
    fireEvent.click(screen.getByText("expand"));
    expect(screen.getByText("FY2023: x unmapped")).toBeTruthy();
  });

  it("never folds the hard coverage warning", () => {
    const ws = [1, 2, 3, 4, 5].map(() =>
      warning({ code: "coverage_low", message: "coverage 62% — hard warning" }),
    );
    render(<Caveats warnings={ws} checks={[]} />);
    expect(screen.getAllByText(/coverage 62%/).length).toBe(5);
  });

  it("flips to a loud failed state when a check fails", () => {
    render(
      <Caveats
        warnings={[]}
        checks={[
          { id: "P1", severity: "hard", status: "fail", magnitude: 2, detail: "BS off" },
        ]}
      />,
    );
    expect(screen.getByText(/check\(s\) FAILED/)).toBeTruthy();
  });
});

describe("state card", () => {
  it("renders a refusal as first-class content with machine detail", () => {
    render(
      <StateCard
        kicker="Refused — the model would not be trustworthy"
        title="DE refused: insufficient coverage"
        message="Only 42% of assets could be attributed."
        detail={'largest: "OtherAssets $18.4B"'}
      />,
    );
    expect(screen.getByText(/insufficient coverage/)).toBeTruthy();
    expect(screen.getByText(/OtherAssets/)).toBeTruthy();
    expect(screen.getByText("← Another ticker")).toBeTruthy();
  });
});
