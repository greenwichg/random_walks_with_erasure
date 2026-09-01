/**
 * Consecutive-run grouping — what the coverage list's collapsed rows stand on.
 *
 * Mutation ledger (each red against the listed break of coverage-groups.ts):
 *  - grouping by publisher globally (Map) -> "a reappearance starts a NEW group" fails
 *  - keeping the last row as lead         -> "lead is the first row of the run" fails
 *  - dropping rest rows                   -> "nothing is lost" fails (flatten mismatch)
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { collapseConsecutive } from "./coverage-groups.ts";
import type { StoryCoverage } from "../domain/types.ts";

const row = (publisher: string, at: string): StoryCoverage =>
  ({ publisher, headline: `h-${publisher}-${at}`, publishedAt: at }) as StoryCoverage;

test("a consecutive run collapses to its first row plus the rest, in order", () => {
  const g = collapseConsecutive([row("BBC", "9"), row("BBC", "8"), row("BBC", "7"), row("NPR", "6")]);
  assert.equal(g.length, 2);
  assert.equal(g[0].lead.publishedAt, "9");
  assert.deepEqual(g[0].rest.map((r) => r.publishedAt), ["8", "7"]);
  assert.deepEqual(g[1], { lead: row("NPR", "6"), rest: [] });
});

test("a reappearance starts a NEW group — chronology is never reshuffled", () => {
  const g = collapseConsecutive([row("BBC", "9"), row("NPR", "8"), row("BBC", "7")]);
  assert.deepEqual(g.map((x) => [x.lead.publisher, x.rest.length]), [["BBC", 0], ["NPR", 0], ["BBC", 0]]);
});

test("nothing is lost: groups flatten back to exactly the input", () => {
  const input = [row("A", "9"), row("A", "8"), row("B", "7"), row("A", "6"), row("A", "5"), row("A", "4")];
  const flat = collapseConsecutive(input).flatMap((x) => [x.lead, ...x.rest]);
  assert.deepEqual(flat, input);
});

test("empty in, empty out", () => {
  assert.deepEqual(collapseConsecutive([]), []);
});
