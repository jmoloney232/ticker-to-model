/* Contract-state rendering: unavailable legs, folded/hard caveats, refusal
   cards — the states the owner requires to be designed, not accidental. */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { modelOk, warning } from "../test-fixtures";
import { Caveats } from "./Caveats";
import { EpvHero } from "./EpvHero";
import { Hero } from "./Hero";
import { StateCard } from "./StateCard";

afterEach(cleanup);

describe("hero legs", () => {
  it("renders an unavailable leg as a reasoned plate, never an error", () => {
    const m = modelOk();
    const i = m.valuation.findIndex((mo) => mo.id === "exit_multiple");
    m.valuation[i] = {
      id: "exit_multiple",
      label: "DCF — exit multiple",
      family: "dcf",
      note: "",
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

  it("keeps the families separate: no EPV bar in the DCF hero", () => {
    render(<Hero model={modelOk()} />);
    expect(screen.getByText("280.99")).toBeTruthy();
    expect(screen.queryByText("171.22")).toBeNull();
    expect(screen.queryByText("Earnings power (no growth)")).toBeNull();
  });

  it("renders the EPV hero from its own family — margin, value, price", () => {
    render(<EpvHero model={modelOk()} />);
    expect(screen.getByText("171.22")).toBeTruthy();
    expect(screen.getByText("Earnings power (no growth)")).toBeTruthy();
    expect(screen.getByText("−65.6%")).toBeTruthy();
    expect(screen.getByText(/normalized operating margin/)).toBeTruthy();
    // no DCF concepts leak in
    expect(screen.queryByText("280.99")).toBeNull();
    expect(screen.queryByText(/perpetuity/)).toBeNull();
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

  it("renders an info item as a quiet note, distinct from a warning", () => {
    render(
      <Caveats
        warnings={[
          warning({ message: "FY2024: lease liability is 31% of debt." }),
          warning({
            origin: "engine",
            code: "terminal_excess_return_persistent",
            severity: "info",
            message: "Terminal ROIC holds 35.7pp above WACC in perpetuity.",
          }),
        ]}
        checks={[]}
      />,
    );
    expect(screen.getByText(/Caveats — 1 · 1 note, model valid/)).toBeTruthy();
    expect(screen.getAllByText("△").length).toBe(1); // warn glyph only
    expect(screen.getAllByText("○").length).toBe(1); // note glyph only
    const note = screen
      .getByText(/Terminal ROIC holds/)
      .closest(".caveat-row");
    expect(note?.className).toContain("note");
    expect(note?.textContent).toContain("engine · note");
  });

  it("orders notes after warnings and never folds them into a warn group", () => {
    const ws = [
      ...[1, 2].map((i) => warning({ severity: "info", message: `note ${i}` })),
      ...[1, 2].map((i) => warning({ message: `warn ${i}` })),
    ];
    render(<Caveats warnings={ws} checks={[]} />);
    // 4 rows share origin+code but split 2/2 by severity: nothing folds
    expect(screen.queryByText(/4× optional unmapped/)).toBeNull();
    const texts = [...document.querySelectorAll(".caveat-row .text")].map(
      (n) => n.textContent,
    );
    expect(texts).toEqual(["warn 1", "warn 2", "note 1", "note 2"]);
  });

  it("folds repeated notes into their own disclosure, labeled as notes", () => {
    const ws = [
      warning({ message: "the lone warning" }),
      ...[1, 2, 3, 4].map((y) =>
        warning({
          severity: "info",
          fiscal_year: 2020 + y,
          message: `FY${2020 + y}: informational`,
        }),
      ),
    ];
    render(<Caveats warnings={ws} checks={[]} />);
    expect(screen.getByText(/Caveats — 1 · 4 notes/)).toBeTruthy();
    expect(screen.getByText(/4× optional unmapped \(notes\)/)).toBeTruthy();
    expect(screen.getByText("the lone warning")).toBeTruthy();
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
