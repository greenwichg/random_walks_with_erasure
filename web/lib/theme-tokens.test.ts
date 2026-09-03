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

/** globals.css, read as text for the same reason the config is. */
const CSS = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "app", "globals.css"),
  "utf-8",
);

describe("desktop surface tier", () => {
  it("scopes each theme's overrides so they cannot leak into the other", () => {
    // `:root` and `.dark` share specificity (0,1,0), so a bare `:root` block inside the desktop
    // media query — which sits after `.dark` in the file — wins in BOTH themes. That shipped
    // once: the light tier's `--accent` (89% lightness) landed under the dark theme's
    // `--accent-foreground` (92%) and every topic chip became pale-on-pale. The `:not(.dark)` /
    // `.dark` pair is what keeps each tier inside its own theme.
    const at = CSS.indexOf("@media (min-width: 1024px)");
    assert.ok(at >= 0, "globals.css has no desktop surface tier");
    const block = CSS.slice(at, CSS.indexOf("\n  }\n}", at));
    assert.ok(
      block.includes(":root:not(.dark)"),
      "the light desktop tier must be scoped `:root:not(.dark)`, or it also repaints dark mode",
    );
    assert.ok(
      block.includes(":root.dark"),
      "the dark desktop tier must be scoped `:root.dark`",
    );
    assert.ok(
      !/\n\s*:root\s*\{/.test(block),
      "an unscoped `:root` block inside the desktop tier overrides BOTH themes",
    );
  });

  it("keeps the card surface distinct from the page surface in both desktop themes", () => {
    // The whole tier rests on this: tiles are `--card`, the page is `--background`. If a theme
    // ever set them to the same value the desktop layout would flatten into one sheet.
    for (const [scope, expectedPage] of [
      [":root:not(.dark)", "220 10% 94%"],
      [":root.dark", "220 7% 7%"],
    ] as const) {
      const at = CSS.indexOf(scope, CSS.indexOf("@media (min-width: 1024px)"));
      assert.ok(at >= 0, `the desktop tier defines ${scope}`);
      const body = CSS.slice(at, CSS.indexOf("}", at));
      assert.ok(
        body.includes(`--background: ${expectedPage}`),
        `${scope} sets the desktop page surface`,
      );
    }
  });
});

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
