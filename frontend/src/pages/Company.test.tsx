/* Page-level: the fetch loop against a stubbed API — happy path binds API
   values verbatim (no client math), tabs switch in place without navigating,
   the detail toggle restores full density, the slider posts exact curve
   points, and refusals lead with the server's plain-English verdict. */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { modelOk } from "../test-fixtures";
import { Company } from "./Company";

function stubFetch(modelResponse: unknown) {
  const calls: { url: string; body: unknown }[] = [];
  const fn = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({
      url,
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    const ok = (body: unknown) =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    if (url.includes("/api/presets")) return ok({ presets: [] });
    if (url.includes("/api/methodology"))
      return ok({ meta: {}, conventions: [], presets: [] });
    if (url.includes("/api/audit-guide")) return ok({ markdown: "# Guide" });
    if (url.includes("/api/model/")) return ok(modelResponse);
    return ok({});
  });
  return { fn, calls };
}

beforeEach(() => {
  window.history.replaceState(null, "", "/company/MSFT");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Company page — Summary tab", () => {
  it("renders API values verbatim and links the workbook to the same code", async () => {
    vi.stubGlobal("fetch", stubFetch(modelOk()).fn);
    render(<Company ticker="MSFT" />);
    await waitFor(() =>
      expect(screen.getAllByText("280.99").length).toBeGreaterThan(0),
    );
    expect(screen.getByText("606.28")).toBeTruthy();
    // server's vs_price, in the hero and the slider readout — never recomputed
    expect(screen.getAllByText("−43.6%").length).toBeGreaterThan(0);
    const dl = screen.getByText(/Download workbook/i).closest("a");
    expect(dl?.getAttribute("href")).toContain("code=abc123");
  });

  it("prints the server verdict sentence, never its own", async () => {
    vi.stubGlobal("fetch", stubFetch(modelOk()).fn);
    render(<Company ticker="MSFT" />);
    await waitFor(() =>
      expect(
        screen.getByText(/Microsoft is worth \$281 a share/),
      ).toBeTruthy(),
    );
    expect(screen.getByText(/6\.2% growth forever/)).toBeTruthy();
  });

  it("ranks the drivers with direction cues", async () => {
    vi.stubGlobal("fetch", stubFetch(modelOk()).fn);
    render(<Company ticker="MSFT" />);
    await waitFor(() =>
      expect(screen.getByText("Discount rate (WACC)")).toBeTruthy(),
    );
    expect(screen.getAllByText("↑ input → ↓ value").length).toBe(1);
    expect(screen.getByText(/±1pp ⇒ ∓\$37\/sh/)).toBeTruthy();
  });
});

describe("tabs — in place, no navigation", () => {
  it("switches to Model and back without touching the pathname", async () => {
    vi.stubGlobal("fetch", stubFetch(modelOk()).fn);
    render(<Company ticker="MSFT" />);
    await waitFor(() => expect(screen.getByRole("tab", { name: /model/i })).toBeTruthy());
    // assumptions table is NOT on the summary tab
    expect(screen.queryByLabelText("Revenue growth fy1")).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: /model/i }));
    expect(screen.getByLabelText("Revenue growth fy1")).toBeTruthy();
    expect(window.location.pathname).toBe("/company/MSFT");
    expect(window.location.search).toContain("tab=model");
    fireEvent.click(screen.getByRole("tab", { name: /summary/i }));
    expect(screen.queryByLabelText("Revenue growth fy1")).toBeNull();
  });

  it("shows codes on the Audit tab", async () => {
    const m = modelOk({
      warnings: [
        {
          origin: "ingest",
          code: "week53",
          message: "FY2023 is a 53-week year.",
          fiscal_year: 2023,
          item: null,
          severity: "warn",
          detail: {},
        },
      ],
    });
    vi.stubGlobal("fetch", stubFetch(m).fn);
    render(<Company ticker="MSFT" />);
    await waitFor(() => expect(screen.getByRole("tab", { name: /audit/i })).toBeTruthy());
    fireEvent.click(screen.getByRole("tab", { name: /audit/i }));
    expect(screen.getByText("week53")).toBeTruthy(); // the machine code chip
  });
});

describe("detail toggle", () => {
  it("restores full density on Summary in one click", async () => {
    vi.stubGlobal("fetch", stubFetch(modelOk()).fn);
    render(<Company ticker="MSFT" />);
    await waitFor(() =>
      expect(screen.getByText("Discount rate (WACC)")).toBeTruthy(),
    );
    expect(screen.queryByLabelText("Revenue growth fy1")).toBeNull();
    fireEvent.click(screen.getByText("full detail"));
    // the wall of numbers is back, inline, verdict still present
    expect(screen.getByLabelText("Revenue growth fy1")).toBeTruthy();
    expect(screen.getByText(/Microsoft is worth \$281 a share/)).toBeTruthy();
    expect(screen.queryByText("Discount rate (WACC)")).toBeNull();
  });
});

describe("the slider", () => {
  it("reads engine curve points and posts the exact point on commit", async () => {
    const stub = stubFetch(modelOk());
    vi.stubGlobal("fetch", stub.fn);
    render(<Company ticker="MSFT" />);
    await waitFor(() =>
      expect(screen.getByRole("slider")).toBeTruthy(),
    );
    const slider = screen.getByRole("slider");
    // at rest: the curve point AT the current g — identical to the hero
    expect(slider.getAttribute("aria-valuetext")).toContain("280.99");
    // one step right lands on the next precomputed point (the rf landmark)
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    await waitFor(() => {
      const posts = stub.calls.filter((c) => c.url.includes("/api/model/"));
      const last = posts[posts.length - 1].body as {
        overrides?: Record<string, number>;
      };
      expect(last.overrides?.terminal_growth).toBe(0.0468);
    });
  });

  it("states the reason when no curve exists instead of rendering", async () => {
    const m = modelOk({ curves: {} });
    m.valuation.gordon = {
      available: false,
      reason: {
        code: "terminal_anchor_negative",
        message: "Terminal-year cash flow is negative.",
        detail: {},
      },
    };
    vi.stubGlobal("fetch", stubFetch(m).fn);
    render(<Company ticker="BA" />);
    await waitFor(() =>
      expect(
        screen.getByText(/No slider here: Terminal-year cash flow is negative/),
      ).toBeTruthy(),
    );
    expect(screen.queryByRole("slider")).toBeNull();
  });
});

describe("blocked states", () => {
  it("renders a refusal with the plain verdict leading and the technical reason kept", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        status: "refused",
        verdict:
          "DE can't be modeled honestly from its filings: only 54% of assets tie to named line items — most of the rest is its financing arm, a lending business this enterprise DCF can't price. Rather than guess, this tool declines.",
        reason: {
          code: "insufficient_coverage",
          message: "Only 42% of assets could be attributed to named items.",
          detail: {},
        },
      }).fn,
    );
    render(<Company ticker="DE" />);
    await waitFor(() =>
      expect(screen.getByText(/financing arm, a lending business/)).toBeTruthy(),
    );
    expect(screen.getByText(/Only 42% of assets/)).toBeTruthy();
    expect(screen.queryByText(/Download workbook/i)).toBeNull();
  });

  it("offers the way back when a preset is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        status: "preset_unavailable",
        reason: {
          code: "preset_unavailable",
          message: "No terminal growth below WACC reaches the market price.",
          detail: {},
        },
      }).fn,
    );
    render(<Company ticker="MSFT" />);
    await waitFor(() =>
      expect(screen.getByText(/No terminal growth below WACC/)).toBeTruthy(),
    );
    expect(screen.getByText("Return to derived defaults")).toBeTruthy();
  });
});
