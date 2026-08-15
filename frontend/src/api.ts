/* Thin fetch client. Refusals and unavailable states arrive as 200s and pass
   through as data; HTTP errors become ApiError with the server's own message
   (owner status rules). No secrets, no vendor calls — same-origin /api only. */

import type { MethodologyDoc, ModelResponse, PresetInfo } from "./types";

const BASE: string = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (res.ok) return (await res.json()) as T;
  let detail = `${res.status}`;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    /* non-JSON error body: keep the status text */
  }
  throw new ApiError(res.status, detail);
}

export interface ModelRequest {
  preset?: string | null;
  overrides?: Record<string, number | boolean>;
  profile?: string | null; // reassignment tag; null/absent = auto-classified
  code?: string | null;
}

export function fetchModel(
  ticker: string,
  req: ModelRequest,
  signal?: AbortSignal,
): Promise<ModelResponse> {
  return fetch(`${BASE}/api/model/${encodeURIComponent(ticker)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  }).then((r) => handle<ModelResponse>(r));
}

export function fetchPresets(): Promise<{ presets: PresetInfo[] }> {
  return fetch(`${BASE}/api/presets`).then((r) =>
    handle<{ presets: PresetInfo[] }>(r),
  );
}

export function fetchMethodology(): Promise<MethodologyDoc> {
  return fetch(`${BASE}/api/methodology`).then((r) =>
    handle<MethodologyDoc>(r),
  );
}

export function fetchAuditGuide(): Promise<{ markdown: string }> {
  return fetch(`${BASE}/api/audit-guide`).then((r) =>
    handle<{ markdown: string }>(r),
  );
}

export function decodeCode(code: string): Promise<{
  preset: string | null;
  overrides: Record<string, number | boolean>;
  profile: string | null;
}> {
  return fetch(`${BASE}/api/code/${encodeURIComponent(code)}`).then((r) =>
    handle<{
      preset: string | null;
      overrides: Record<string, number | boolean>;
      profile: string | null;
    }>(r),
  );
}

export function workbookUrl(ticker: string, code: string): string {
  const q = code ? `?code=${encodeURIComponent(code)}` : "";
  return `${BASE}/api/workbook/${encodeURIComponent(ticker)}.xlsx${q}`;
}
