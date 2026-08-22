import assert from "node:assert/strict";
import { test } from "node:test";

import { leanBucket, leanLabelKey, leanToPercent, personalBlindspotSide } from "./political.ts";

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

test("personalBlindspotSide names the reader's OWN heavy side — that is the lens semantics", () => {
  // ?blindspot=left lists stories THIN on the left: what a left-heavy diet missed.
  assert.equal(personalBlindspotSide({ left: 0.6, center: 0.3, right: 0.1 }), "left");
  assert.equal(personalBlindspotSide({ left: 0.1, center: 0.3, right: 0.6 }), "right");
});

test("personalBlindspotSide: near-balanced diets make no claim", () => {
  assert.equal(personalBlindspotSide({ left: 0.4, center: 0.2, right: 0.4 }), null);
  assert.equal(personalBlindspotSide({ left: 0.45, center: 0.2, right: 0.35 }), null); // 10pt < 15pt
  // Center-heavy is exposure, not a skew: no lens.
  assert.equal(personalBlindspotSide({ left: 0.1, center: 0.8, right: 0.1 }), null);
});

test("personalBlindspotSide: threshold is inclusive at exactly minSkew and tunable", () => {
  assert.equal(personalBlindspotSide({ left: 0.5, center: 0.15, right: 0.35 }), "left"); // 15pt
  assert.equal(personalBlindspotSide({ left: 0.5, center: 0.15, right: 0.35 }, 0.2), null);
});
