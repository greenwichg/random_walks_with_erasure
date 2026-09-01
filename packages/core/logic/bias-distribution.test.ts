/**
 * Bias distribution grouping — the claims the story panel's outlet columns stand on.
 *
 * Mutation ledger (each check went red against the listed break of bias-distribution.ts):
 *  - counting rows instead of outlets            -> "one mark per outlet" fails (3 marks, not 1)
 *  - null-guard dropped (every row overwrites)   -> "null row must never unrate" fails
 *  - untracked pushed into center                -> "unknown is not neutral" fails
 *  - DOMINANT_ORDER reversed                     -> "even split headlines center" fails
 *  - ratedCount includes untracked               -> "unknown is not neutral" and "nothing rated" fail
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { dominantBucket, groupOutletsByLean } from "./bias-distribution.ts";
import type { StoryCoverage } from "../domain/types.ts";

const row = (publisher: string, leanBucket: StoryCoverage["leanBucket"], url?: string): StoryCoverage =>
  ({ publisher, headline: "h", publishedAt: "2026-09-01T00:00:00+00:00", leanBucket, url }) as StoryCoverage;

test("one mark per outlet, however many articles it filed", () => {
  const g = groupOutletsByLean([
    row("Fox News", "right"),
    row("Fox News", "right"),
    row("Fox News", "right"),
  ]);
  assert.equal(g.buckets.right.length, 1);
  assert.equal(g.ratedCount, 1);
});

test("a null row must never unrate the outlet, whichever side of the rated row it lands", () => {
  for (const rows of [
    [row("Reuters", null), row("Reuters", "center")],
    [row("Reuters", "center"), row("Reuters", null)],
  ]) {
    const g = groupOutletsByLean(rows);
    assert.equal(g.buckets.center.length, 1);
    assert.equal(g.untracked.length, 0);
  }
});

test("an unrated outlet is untracked, never center — unknown is not neutral", () => {
  const g = groupOutletsByLean([row("Youm7", null), row("CNN", "left")]);
  assert.deepEqual(g.untracked, [{ publisher: "Youm7", url: undefined }]);
  assert.equal(g.buckets.center.length, 0);
  assert.equal(g.ratedCount, 1);
});

test("marks keep first-seen (newest-first) order and carry the outlet's first URL for icons", () => {
  const g = groupOutletsByLean([
    row("AP", "center", "https://apnews.com/a1"),
    row("CNN", "left", "https://cnn.com/a2"),
    row("AP", "center", "https://apnews.com/a3"),
  ]);
  assert.deepEqual(g.buckets.center, [{ publisher: "AP", url: "https://apnews.com/a1" }]);
  assert.deepEqual(g.buckets.left, [{ publisher: "CNN", url: "https://cnn.com/a2" }]);
});

test("dominant: an even split headlines center, and the share is an outlet share", () => {
  const even = groupOutletsByLean([
    row("CNN", "left"),
    row("AP", "center"),
    row("Fox News", "right"),
  ]);
  assert.deepEqual(dominantBucket(even), { bucket: "center", pct: 33 });

  const mostlyCenter = groupOutletsByLean([
    row("AP", "center"),
    row("Reuters", "center"),
    row("CNN", "left"),
  ]);
  assert.deepEqual(dominantBucket(mostlyCenter), { bucket: "center", pct: 67 });
});

test("nothing rated -> no headline fact, not a fabricated 100% something", () => {
  assert.equal(dominantBucket(groupOutletsByLean([row("Youm7", null)])), null);
  assert.equal(dominantBucket(groupOutletsByLean([])), null);
});
