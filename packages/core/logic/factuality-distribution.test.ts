/**
 * Factuality grouping — the claims the Factuality tab's chart, legend, credit line and full
 * breakdown stand on.
 *
 * Mutation ledger (each check went red against the listed break of factuality-distribution.ts):
 *  - counting rows instead of outlets             -> "one mark per outlet" fails (3, not 1)
 *  - VALID gate dropped (any token is a slice)    -> "outside the scale" fails
 *  - unrated folded into `mixed`                  -> "unrated is not a level" fails
 *  - slices in arrival order, not FACTUALITY_ORDER-> "rater's own order" fails
 *  - dominant pct over ratedCount                 -> "share over ALL outlets" fails (50 -> 100)
 *  - unrated allowed to win dominant              -> "unrated cannot headline" fails
 *  - attribution takes the NEWEST asOf            -> "oldest date" fails (2026-08-11, not -07-28)
 *  - marks drop their rating                      -> "every mark keeps its attribution" fails
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  FACTUALITY_ORDER,
  dominantFactuality,
  factualityAttribution,
  groupOutletsByFactuality,
} from "./factuality-distribution.ts";
import type { StoryCoverage } from "../domain/types.ts";

const rate = (value: string, asOf = "2026-08-10") =>
  ({ value, source: "mbfc", asOf, ratingUrl: "https://example.test/?s=x" }) as never;

const row = (publisher: string, value: string | null, asOf?: string): StoryCoverage =>
  ({ publisher, headline: "h", publishedAt: "2026-09-01T00:00:00+00:00",
     factuality: value ? rate(value, asOf) : null }) as StoryCoverage;

test("one mark per outlet, however many articles it filed", () => {
  const g = groupOutletsByFactuality([
    row("BBC", "high"),
    row("BBC", "high"),
    row("BBC", "high"),
  ]);
  assert.deepEqual(g.slices.map((s) => [s.level, s.outlets.length]), [["high", 1]]);
  assert.equal(g.totalOutlets, 1);
  assert.equal(g.ratedCount, 1);
});

test("a null row must never unrate the outlet, whichever side it lands", () => {
  for (const rows of [
    [row("NPR", null), row("NPR", "high")],
    [row("NPR", "high"), row("NPR", null)],
  ]) {
    assert.deepEqual(groupOutletsByFactuality(rows).slices.map((s) => s.level), ["high"]);
  }
});

test("a level outside the rater's scale renders as unrated, never as a new slice", () => {
  const g = groupOutletsByFactuality([row("Weird Wire", "excellent")]);
  assert.deepEqual(g.slices.map((s) => s.level), ["unrated"]);
  assert.equal(g.ratedCount, 0);
});

test("unrated is not a level: an unrated outlet lands in the muted last slice", () => {
  const g = groupOutletsByFactuality([row("Youm7", null), row("BBC", "high")]);
  assert.deepEqual(g.slices.map((s) => s.level), ["high", "unrated"]);
  assert.equal(g.slices[1]?.outlets[0]?.publisher, "Youm7");
  assert.equal(g.slices[1]?.outlets[0]?.rating, undefined);
});

test("slices keep the rater's own order regardless of arrival order, unrated always last", () => {
  const g = groupOutletsByFactuality([
    row("Youm7", null),
    row("Daily Mail", "mixed"),
    row("BBC", "high"),
    row("Reuters", "very_high"),
    row("Fox News", "low"),
  ]);
  assert.deepEqual(
    g.slices.map((s) => s.level),
    ["very_high", "high", "mixed", "low", "unrated"],
  );
  // The six levels are the rater's scale, never collapsed into three.
  assert.equal(FACTUALITY_ORDER.length, 6);
  assert.equal(FACTUALITY_ORDER.indexOf("mostly_factual") < FACTUALITY_ORDER.indexOf("mixed"), true);
});

test("dominant: the largest RATED level, its share over ALL outlets incl. unrated", () => {
  const g = groupOutletsByFactuality([
    row("BBC", "high"),
    row("NPR", "high"),
    row("Youm7", null),
    row("Kenh14", null),
  ]);
  assert.deepEqual(dominantFactuality(g), { level: "high", pct: 50 });
});

test("unrated cannot headline, even as a majority; nothing rated -> no headline at all", () => {
  const g = groupOutletsByFactuality([
    row("Youm7", null),
    row("Kenh14", null),
    row("Dan Tri", null),
    row("BBC", "high"),
  ]);
  assert.deepEqual(dominantFactuality(g), { level: "high", pct: 25 });
  assert.equal(dominantFactuality(groupOutletsByFactuality([row("Youm7", null)])), null);
  assert.equal(dominantFactuality(groupOutletsByFactuality([])), null);
});

test("a tie goes to the rater's BETTER level — the conservative direction", () => {
  const g = groupOutletsByFactuality([row("BBC", "high"), row("Daily Mail", "mixed")]);
  assert.deepEqual(dominantFactuality(g), { level: "high", pct: 50 });
});

test("every rated mark keeps the verdict that produced it, so the list can attribute per outlet", () => {
  const g = groupOutletsByFactuality([row("BBC", "high", "2026-08-11")]);
  assert.deepEqual(g.slices[0]?.outlets[0]?.rating, {
    value: "high", source: "mbfc", asOf: "2026-08-11", ratingUrl: "https://example.test/?s=x",
  });
});

test("the credit line names every rater and dates itself to the OLDEST verdict shown", () => {
  const g = groupOutletsByFactuality([
    row("BBC", "high", "2026-08-11"),
    row("NPR", "high", "2026-07-28"),
    row("Youm7", null),
  ]);
  assert.deepEqual(factualityAttribution(g), { sources: ["mbfc"], asOf: "2026-07-28" });
});

test("no verdicts -> no credit line: there is nobody to credit", () => {
  assert.equal(factualityAttribution(groupOutletsByFactuality([row("Youm7", null)])), null);
  assert.equal(factualityAttribution(groupOutletsByFactuality([])), null);
});
