// The palette, checked against the stylesheet it was transcribed from.
//
// `design/tokens.ts` says its values come from `web/app/globals.css`. That claim is worth exactly
// as much as a test: the web keeps HSL triples in CSS custom properties, native needs hex, and a
// hand conversion is a place where a digit goes missing and nobody notices because the result still
// looks like the right colour.
//
// The lean colours are the ones that matter most. `--left`, `--center` and `--right` are how a
// reader reads a coverage plate, and a native app whose "left" was a few degrees off would be
// telling a quietly different story about the same article — the kind of drift
// docs/SIGNAL_INTEGRITY.md exists to prevent.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { dark, light, leanColor, space, type } from "./tokens.ts";

const CSS = readFileSync(
  join(import.meta.dirname, "..", "..", "web", "app", "globals.css"),
  "utf8",
);

/** `--primary: 250 60% 52%` → [250, 60, 52], from the light or dark block. */
function hsl(name: string, mode: "light" | "dark"): [number, number, number] {
  // Dark is a `.dark` CLASS, not a `prefers-color-scheme` media query — next-themes toggles it.
  // The first draft of this test split on the media query, found no dark block at all, and reported
  // every dark token as wrong. Splitting on the wrong thing is a way to fail that looks like a
  // finding, so the marker is asserted rather than assumed.
  const marker = CSS.indexOf(".dark {");
  assert.ok(marker > 0, "globals.css has no `.dark {` block — the split below would be meaningless");
  const source = mode === "light" ? CSS.slice(0, marker) : CSS.slice(marker);
  const m = new RegExp(`--${name}:\\s*([\\d.]+)\\s+([\\d.]+)%\\s+([\\d.]+)%`).exec(source);
  assert.ok(m, `--${name} not found in the ${mode} block of globals.css`);
  return [Number(m![1]), Number(m![2]), Number(m![3])];
}

/** The standard conversion. Written out rather than imported so the test does not share a bug. */
function hslToHex([h, s, l]: [number, number, number]): string {
  const sat = s / 100;
  const lig = l / 100;
  const c = (1 - Math.abs(2 * lig - 1)) * sat;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = lig - c / 2;
  const [r, g, b] =
    h < 60 ? [c, x, 0] :
    h < 120 ? [x, c, 0] :
    h < 180 ? [0, c, x] :
    h < 240 ? [0, x, c] :
    h < 300 ? [x, 0, c] : [c, 0, x];
  const to = (v: number) => Math.round((v + m) * 255).toString(16).padStart(2, "0");
  return `#${to(r)}${to(g)}${to(b)}`;
}

/** Channel-wise distance, so a rounding difference of one step is not reported as a drift. */
function channels(hex: string): [number, number, number] {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number];
}
function within(a: string, b: string, tolerance = 2): boolean {
  const [ar, ag, ab] = channels(a);
  const [br, bg, bb] = channels(b);
  return Math.abs(ar - br) <= tolerance && Math.abs(ag - bg) <= tolerance && Math.abs(ab - bb) <= tolerance;
}

const TOKENS: Array<[keyof typeof light, string]> = [
  ["background", "background"],
  ["foreground", "foreground"],
  ["card", "card"],
  ["primary", "primary"],
  ["primaryForeground", "primary-foreground"],
  ["muted", "muted"],
  ["mutedForeground", "muted-foreground"],
  ["border", "border"],
  ["left", "left"],
  ["center", "center"],
  ["right", "right"],
  ["positive", "positive"],
  ["caution", "caution"],
];

test("the light palette is the stylesheet's light palette", () => {
  for (const [key, cssName] of TOKENS) {
    const expected = hslToHex(hsl(cssName, "light"));
    assert.ok(
      within(light[key], expected),
      `light.${key} is ${light[key]}, but --${cssName} in globals.css is ${expected}`,
    );
  }
});

test("the dark palette is the stylesheet's dark palette", () => {
  for (const [key, cssName] of TOKENS) {
    const expected = hslToHex(hsl(cssName, "dark"));
    assert.ok(
      within(dark[key], expected),
      `dark.${key} is ${dark[key]}, but --${cssName} in the dark block is ${expected}`,
    );
  }
});

test("every token is a full-length hex colour", () => {
  // A three-digit shorthand or a stray "hsl(...)" would render on one platform and not the other.
  for (const palette of [light, dark]) {
    for (const [key, value] of Object.entries(palette)) {
      assert.match(value, /^#[0-9a-f]{6}$/, `${key} = ${value}`);
    }
  }
});

test("an unrated outlet gets NO lean colour — never Center", () => {
  // Unrated outlets are common (L2.2), and colouring one grey would be a fabricated claim about a
  // publisher's politics rather than an absence of one. The web renders "Unknown"; so does this.
  assert.equal(leanColor(null, light), null);
  assert.equal(leanColor(undefined, light), null);
  assert.equal(leanColor("", light), null);
  assert.equal(leanColor("unknown", light), null);
  assert.equal(leanColor("center", light), light.center);
  assert.equal(leanColor("left", light), light.left);
  assert.equal(leanColor("right", light), light.right);
});

test("the spacing grid has no odd values", () => {
  for (const [name, value] of Object.entries(space)) {
    assert.equal(value % 4, 0, `space.${name} = ${value} is off the 4pt grid`);
  }
});

test("the type scale ascends and every step has a line height", () => {
  const order = ["label", "caption", "body", "headline", "title", "display"] as const;
  for (let i = 1; i < order.length; i++) {
    assert.ok(
      type[order[i]].fontSize >= type[order[i - 1]].fontSize,
      `${order[i]} is not larger than ${order[i - 1]}`,
    );
  }
  for (const [name, step] of Object.entries(type)) {
    assert.ok(step.lineHeight > step.fontSize, `${name} has no leading`);
  }
});
