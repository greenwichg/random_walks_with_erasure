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

import { dominantBucket, groupOutletsByLean, splitAtCap } from "./bias-distribution.ts";
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

test("splitAtCap: the +N chip's number is exactly what the panel behind it lists", () => {
  // The contract the capsule and its overflow panel share. Mutation ledger:
  //  - slice(cap + 1) / slice(cap - 1) on either half -> the sum or the counts here fail
  //  - hidden computed as outlets.slice(0, -cap)      -> the 6-of-16 case fails
  const outlets = Array.from({ length: 16 }, (_, i) => ({ publisher: `P${i}`, url: undefined }));
  const { shown, hidden } = splitAtCap(outlets, 5);
  assert.equal(shown.length, 5);
  assert.equal(hidden.length, 11, "the chip says +11, so the panel must list 11");
  assert.deepEqual([...shown, ...hidden], outlets, "nothing is lost or duplicated at the seam");
  assert.equal(hidden[0].publisher, "P5", "the panel starts where the capsule stopped");
});

test("splitAtCap: a group that fits has nothing hidden, so no chip is drawn", () => {
  const outlets = [{ publisher: "A", url: undefined }, { publisher: "B", url: undefined }];
  assert.deepEqual(splitAtCap(outlets, 5), { shown: outlets, hidden: [] });
  assert.deepEqual(splitAtCap([], 5), { shown: [], hidden: [] });
});
