import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { splitCoverage } from "./story-attached.ts";
import type { StoryCoverage } from "../domain/types";

const member = (publisher: string): StoryCoverage => ({
  publisher, headline: `${publisher} headline`, publishedAt: "2026-08-30T00:00:00Z",
});
const attachedRow = (publisher: string): StoryCoverage => ({
  ...member(publisher), tierB: true,
});

describe("splitCoverage", () => {
  it("separates attached rows from the panel, preserving order within each half", () => {
    const { panel, attached } = splitCoverage([
      member("Alpha"), member("Beta"), attachedRow("Gamma"), attachedRow("Delta"),
    ]);
    assert.deepEqual(panel.map((r) => r.publisher), ["Alpha", "Beta"]);
    assert.deepEqual(attached.map((r) => r.publisher), ["Gamma", "Delta"]);
  });

  it("treats absent tierB as a member — the engine never sends tierB: false", () => {
    // A member row gains no field on the wire; only a row that literally carries tierB: true is
    // an addendum. Anything else in the attached half would shrink the panel a member belongs to.
    const { panel, attached } = splitCoverage([member("Alpha")]);
    assert.equal(panel.length, 1);
    assert.equal(attached.length, 0);
  });

  it("survives an absent coverage array", () => {
    assert.deepEqual(splitCoverage(undefined), { panel: [], attached: [] });
    assert.deepEqual(splitCoverage(null), { panel: [], attached: [] });
  });
});
