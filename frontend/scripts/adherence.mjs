#!/usr/bin/env node
/* Design-system adherence lint (the Industry DS rule, applied to this repo):
   outside tokens.css, no raw colors and no font-family declarations — every
   color and face comes through var(--…). Structural constants the token
   proposal explicitly keeps at component level (chart geometry, mockup column
   specs) are annotated `ds:` on the same line.
   Registered extensions (owner sign-off 2026-08-14): IBM Plex Mono as
   --font-mono; --warn / --down / --down-on-dark. */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../src", import.meta.url));
const ALLOW_FILE = /tokens\.css$/;
const ALLOW_LINE = /ds:/; // documented component-level constant

const RULES = [
  [/#[0-9a-fA-F]{3,8}\b/, "raw hex color"],
  [/rgba?\(/, "raw rgb()/rgba() color"],
  [/font-family\s*:(?!\s*var\()/, "font-family not routed through a token"],
  [/["'](Barlow|IBM Plex|Helvetica|Arial|Inter|Roboto)/, "hardcoded font name"],
];

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else if (/\.(css|tsx|ts)$/.test(name) && !/\.test\.tsx?$/.test(name)) yield p;
  }
}

let bad = 0;
for (const file of walk(ROOT)) {
  if (ALLOW_FILE.test(file)) continue;
  const lines = readFileSync(file, "utf8").split("\n");
  lines.forEach((line, i) => {
    if (ALLOW_LINE.test(line)) return;
    if (i > 0 && ALLOW_LINE.test(lines[i - 1])) return; // annotation above
    for (const [re, why] of RULES) {
      if (re.test(line)) {
        console.error(
          `${relative(process.cwd(), file)}:${i + 1}: ${why}\n    ${line.trim()}`,
        );
        bad += 1;
      }
    }
  });
}

if (bad > 0) {
  console.error(`\n${bad} adherence violation(s).`);
  process.exit(1);
}
console.log("adherence: clean — all color and type flows through tokens.css");
