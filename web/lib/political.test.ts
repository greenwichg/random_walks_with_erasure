import assert from "node:assert/strict";
import { test } from "node:test";

import { leanBucket, leanLabelKey, leanToPercent } from "./political.ts";

test("leanLabelKey speaks AllSides' own tiers on the registry lattice", () => {
  // One UI value space (docs/LEAN_CONSISTENCY.md F1/F2): the backend serves scored registry
  // leans everywhere — Lean Left/Right at ±1, Left/Right at ±2, cut at the lattice midpoint 1.5.
  assert.equal(leanLabelKey(-1), "lean.leanLeft"); // CNN, NPR, The Guardian
  assert.equal(leanLabelKey(1), "lean.leanRight"); // The Economic Times, Geo TV
  assert.equal(leanLabelKey(-2), "lean.left"); // AllSides Left
  assert.equal(leanLabelKey(2), "lean.right"); // Fox News, NY Post
  assert.equal(leanLabelKey(0), "lean.center"); // BBC, AP, Reuters
  // boundaries: sided at |lean| > 0.5 (bucket), full at |lean| >= 1.5 (inclusive)
  assert.equal(leanLabelKey(-1.5), "lean.left");
  assert.equal(leanLabelKey(1.5), "lean.right");
  assert.equal(leanLabelKey(1.4), "lean.leanRight");
  assert.equal(leanLabelKey(0.5), "lean.center"); // bucket is strict > 0.5, unchanged
  // the retired "Strong" tier must never come back — AllSides has no such rating
  for (const v of [-2, -1.9, 1.9, 2]) {
    assert.ok(!leanLabelKey(v).toLowerCase().includes("strong"), String(v));
  }
});

test("leanBucket and leanToPercent are unchanged", () => {
  assert.equal(leanBucket(-1), "left");
  assert.equal(leanBucket(0.5), "center");
  assert.equal(leanBucket(0.51), "right");
  assert.equal(leanToPercent(-2), 0);
  assert.equal(leanToPercent(0), 50);
  assert.equal(leanToPercent(2), 100);
});
