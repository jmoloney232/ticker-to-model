/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev proxy: the browser only ever talks to our own origin; API keys live
// server-side (CLAUDE.md secrets rule). In production the static site points
// at the Render API via VITE_API_BASE.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    globals: false,
    include: ["src/**/*.test.tsx", "src/**/*.test.ts"],
  },
});
