/* Page-level: the fetch loop against a stubbed API — happy path binds API
   values verbatim (no client math), refusals render the designed card, and
   the workbook link carries the same code the screen was built from. */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { modelOk } from "../test-fixtures";
import { Company } from "./Company";

function stubFetch(modelResponse: unknown) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    const ok = (body: unknown) =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    if (url.includes("/api/presets")) return ok({ presets: [] });
    if (url.includes("/api/model/")) return ok(modelResponse);
    return ok({});
  });
}

beforeEach(() => {
  window.history.replaceState(null, "", "/company/MSFT");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Company page", () => {
  it("renders API values verbatim and links the workbook to the same code", async () => {
    vi.stubGlobal("fetch", stubFetch(modelOk()));
    render(<Company ticker="MSFT" />);
    await waitFor(() => expect(screen.getByText("280.99")).toBeTruthy());
    expect(screen.getByText("606.28")).toBeTruthy();
    expect(screen.getByText("−43.6%")).toBeTruthy(); // server's vs_price, not recomputed
    const dl = screen.getByText(/Download workbook/i).closest("a");
    expect(dl?.getAttribute("href")).toContain("code=abc123");
  });

  it("renders a refusal payload as the designed card, no download offered", async () => {
    vi.stubGlobal(
      "fetch",
      stubFetch({
        status: "refused",
        reason: {
          code: "insufficient_coverage",
          message: "Only 42% of assets could be attributed to named items.",
          detail: {},
        },
      }),
    );
    render(<Company ticker="DE" />);
    await waitFor(() =>
      expect(screen.getByText(/insufficient coverage/)).toBeTruthy(),
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
      }),
    );
    render(<Company ticker="MSFT" />);
    await waitFor(() =>
      expect(screen.getByText(/No terminal growth below WACC/)).toBeTruthy(),
    );
    expect(screen.getByText("Return to derived defaults")).toBeTruthy();
  });
});
