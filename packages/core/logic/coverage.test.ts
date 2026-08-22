import { test } from "node:test";
import assert from "node:assert/strict";
import { coverageStatus, METRIC_UNLOCK } from "./coverage.ts";
import type { MetricKey } from "../domain/types.ts";

test("estimate: prefers backend mode, computes remaining + pct", () => {
  const s = coverageStatus("estimate", { reads: 2, threshold: 5, sufficient: false });
  assert.equal(s.isEstimate, true);
  assert.equal(s.reads, 2);
  assert.equal(s.threshold, 5);
  assert.equal(s.remaining, 3);
  assert.equal(s.pct, 40);
  assert.equal(s.sufficient, false);
});

test("measured: not an estimate, no remaining, full progress", () => {
  const s = coverageStatus("measured", { reads: 8, threshold: 5, sufficient: true });
  assert.equal(s.isEstimate, false);
  assert.equal(s.remaining, 0);
  assert.equal(s.pct, 100); // capped even though reads > threshold
});

test("no mode: falls back to coverage.sufficient", () => {
  assert.equal(coverageStatus(undefined, { reads: 5, threshold: 5, sufficient: true }).isEstimate, false);
  assert.equal(coverageStatus(undefined, { reads: 1, threshold: 5, sufficient: false }).isEstimate, true);
  assert.equal(coverageStatus(null, null).isEstimate, true); // nothing known → still building
});

test("missing coverage degrades to 0 of 5, still building", () => {
  const s = coverageStatus(undefined, undefined);
  assert.deepEqual(
    { isEstimate: s.isEstimate, reads: s.reads, threshold: s.threshold, remaining: s.remaining, pct: s.pct },
    { isEstimate: true, reads: 0, threshold: 5, remaining: 5, pct: 0 },
  );
});

test("mode wins over coverage.sufficient when they disagree", () => {
  // A defensive case: mode is the authoritative label.
  assert.equal(coverageStatus("estimate", { reads: 9, threshold: 5, sufficient: true }).isEstimate, true);
  assert.equal(coverageStatus("measured", { reads: 0, threshold: 5, sufficient: false }).isEstimate, false);
});

test("negative/zero inputs are clamped (no NaN, no divide-by-zero)", () => {
  const s = coverageStatus(undefined, { reads: -3, threshold: 0, sufficient: false });
  assert.ok(Number.isFinite(s.pct) && s.pct >= 0 && s.pct <= 100);
  assert.equal(s.reads, 0);
  assert.ok(s.threshold >= 1);
});

test("METRIC_UNLOCK covers all eight metric keys with a known unlock", () => {
  const keys: MetricKey[] = [
    "topicDiversity", "sourceDiversity", "reportingRatio", "emotionalBalance",
    "echoChamber", "viewpointBalance", "openMindedness", "confidence",
  ];
  const allowed = new Set(["unlock.reads", "unlock.political", "unlock.reception"]);
  for (const k of keys) assert.ok(allowed.has(METRIC_UNLOCK[k]), `${k} → ${METRIC_UNLOCK[k]}`);
  assert.equal(METRIC_UNLOCK.openMindedness, "unlock.reception");
  assert.equal(METRIC_UNLOCK.viewpointBalance, "unlock.political");
});
