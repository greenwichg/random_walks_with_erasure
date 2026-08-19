import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

/**
 * A theme colour whose key equals a built-in Tailwind utility name silently shadows that utility
 * — and which one wins is decided by rule order in the generated CSS, so it varies per property.
 * This repo shipped both directions of that bug at once: `left`/`center`/`right` made
 * `.text-left` resolve to `color` (60 alignment usages across the app painted lean hues and never
 * aligned) while `.bg-left` resolved to `background-position` (the report's metric bars lost their
 * fill). The fix was to prefix the keys `lean-*`; this test keeps them prefixed.
 *
 * The config is read as TEXT, not imported: tailwind.config.ts calls `require()` for its plugin,
 * which throws under ESM. Reading the source is also the stricter check — it sees what the author
 * wrote, including keys nested inside colour groups.
 */
const SRC = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "tailwind.config.ts"),
  "utf-8",
);

/** Built-in utility suffixes a colour key would collide with: text-align, background-position,
 *  float/clear, and the global keywords. */
const RESERVED = new Set([
  "left", "center", "right", "justify", "start", "end",
  "top", "bottom", "middle", "none", "auto", "inherit",
]);

/** The `colors: { … }` block, matched to its closing brace by depth counting. */
function colorsBlock(): string {
  const at = SRC.indexOf("colors: {");
  assert.ok(at >= 0, "tailwind.config.ts has no colors block");
  let depth = 0;
  for (let i = SRC.indexOf("{", at); i < SRC.length; i++) {
    if (SRC[i] === "{") depth++;
    else if (SRC[i] === "}" && --depth === 0) return SRC.slice(at, i + 1);
  }
  throw new Error("unbalanced braces in the colors block");
}

describe("tailwind theme colours", () => {
  it("defines no colour whose name shadows a built-in utility", () => {
    const block = colorsBlock();
    const keys = [...block.matchAll(/^\s*"?([A-Za-z][\w-]*)"?\s*:/gm)].map((m) => m[1]);
    assert.ok(keys.length > 5, "the key scan found nothing — the parser drifted");
    const clashes = [...new Set(keys.filter((k) => RESERVED.has(k)))];
    assert.deepEqual(
      clashes,
      [],
      `these colour keys shadow built-in Tailwind utilities: ${clashes.join(", ")}. ` +
        `Prefix them (e.g. lean-left) — otherwise .text-<name> and .bg-<name> silently resolve ` +
        `to whichever rule the generated CSS emits last, and the loser differs per property.`,
    );
  });

  it("keeps the political-lean scale, still reading the unprefixed CSS variables", () => {
    // lib/metrics.ts, the charts and coverage-plate's dynamic `hsl(var(--${token}))` read those
    // variables directly — renaming the VARIABLES rather than the Tailwind keys would have broken
    // every one of them silently.
    for (const side of ["left", "center", "right"]) {
      assert.match(SRC, new RegExp(`"lean-${side}":\\s*"hsl\\(var\\(--${side}\\)\\)"`));
    }
  });
});
