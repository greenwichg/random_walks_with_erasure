/**
 * Ownership grouping — the claims the story panel's bar, ring and legend stand on.
 *
 * Mutation ledger (each check went red against the listed break of ownership-distribution.ts):
 *  - counting rows instead of outlets           -> "one mark per outlet" fails (3, not 1)
 *  - VALID gate dropped (any token is a slice)  -> "outside the vocabulary" fails
 *  - unknown folded into "other"                -> "unknown is not other" fails
 *  - slices in arrival order, not OWNERSHIP_ORDER -> "fixed order" fails
 *  - dominant pct over knownCount               -> "share over ALL outlets" fails (50 -> 100)
 *  - unknown allowed to win dominant            -> "unknown cannot headline" fails
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  OWNERSHIP_ORDER,
  dominantOwnership,
  groupOutletsByOwnership,
} from "./ownership-distribution.ts";
import type { StoryCoverage } from "../domain/types.ts";

const row = (publisher: string, ownership: string | null, url?: string): StoryCoverage =>
  ({ publisher, headline: "h", publishedAt: "2026-09-01T00:00:00+00:00",
     ownership, url }) as StoryCoverage;

test("one mark per outlet, however many articles it filed", () => {
  const g = groupOutletsByOwnership([
    row("Fox News", "conglomerate"),
    row("Fox News", "conglomerate"),
    row("Fox News", "conglomerate"),
  ]);
  assert.deepEqual(g.slices.map((s) => [s.category, s.outlets.length]), [["conglomerate", 1]]);
  assert.equal(g.totalOutlets, 1);
});

test("a null row must never unclassify the outlet, whichever side it lands", () => {
  for (const rows of [
    [row("NPR", null), row("NPR", "independent")],
    [row("NPR", "independent"), row("NPR", null)],
  ]) {
    const g = groupOutletsByOwnership(rows);
    assert.deepEqual(g.slices.map((s) => s.category), ["independent"]);
  }
});

test("a token outside the vocabulary renders as unknown, never as a new slice", () => {
  const g = groupOutletsByOwnership([row("Weird Wire", "oligarch")]);
  assert.deepEqual(g.slices.map((s) => s.category), ["unknown"]);
  assert.equal(g.knownCount, 0);
});

test("unknown is not other: an unclassified outlet lands in the muted last slice", () => {
  const g = groupOutletsByOwnership([row("Youm7", null), row("NPR", "independent")]);
  assert.deepEqual(g.slices.map((s) => s.category), ["independent", "unknown"]);
  assert.deepEqual(g.slices[1].outlets, [{ publisher: "Youm7", url: undefined }]);
});

test("slices keep OWNERSHIP_ORDER regardless of arrival order, unknown always last", () => {
  const g = groupOutletsByOwnership([
    row("Youm7", null),
    row("Fox News", "conglomerate"),
    row("BBC", "government"),
    row("NPR", "independent"),
  ]);
  assert.deepEqual(
    g.slices.map((s) => s.category),
    ["independent", "government", "conglomerate", "unknown"],
  );
  assert.equal(OWNERSHIP_ORDER.indexOf("independent") < OWNERSHIP_ORDER.indexOf("government"), true);
});

test("dominant: the largest KNOWN category, its share over ALL outlets incl. unknown", () => {
  const g = groupOutletsByOwnership([
    row("NPR", "independent"),
    row("Associated Press", "independent"),
    row("Youm7", null),
    row("Kenh14", null),
  ]);
  assert.deepEqual(dominantOwnership(g), { category: "independent", pct: 50 });
});

test("unknown cannot headline, even as a majority; nothing known -> no headline at all", () => {
  const g = groupOutletsByOwnership([
    row("Youm7", null),
    row("Kenh14", null),
    row("Dan Tri", null),
    row("BBC", "government"),
  ]);
  assert.deepEqual(dominantOwnership(g), { category: "government", pct: 25 });
  assert.equal(dominantOwnership(groupOutletsByOwnership([row("Youm7", null)])), null);
  assert.equal(dominantOwnership(groupOutletsByOwnership([])), null);
});

test("a known-category tie goes to the earliest in OWNERSHIP_ORDER", () => {
  const g = groupOutletsByOwnership([
    row("Fox News", "conglomerate"),
    row("NPR", "independent"),
  ]);
  assert.deepEqual(dominantOwnership(g), { category: "independent", pct: 50 });
});
