import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { interleavePublishers } from "./discover-order.ts";

const P = (...names: string[]) => names.map((publisher, i) => ({ publisher, i }));
const order = (items: { publisher?: string }[]) => items.map((x) => x.publisher).join(",");

describe("interleavePublishers", () => {
  it("spreads a feed-poll burst instead of rendering it verbatim", () => {
    // The measured symptom: one outlet filing six rows in a run. The nearest other-publisher
    // item is pulled forward at each repeat — a permutation, never a removal.
    const out = interleavePublishers(P("S", "S", "S", "B", "C", "S"));
    assert.equal(order(out), "S,B,S,C,S,S");
    assert.equal(out.length, 6);
  });

  it("passes an already-diverse list through untouched", () => {
    assert.equal(order(interleavePublishers(P("A", "B", "C", "A"))), "A,B,C,A");
  });

  it("degrades to original order when only one publisher exists (filter active)", () => {
    // A publisher filter makes diversity impossible; the rule must not loop or reorder.
    const out = interleavePublishers(P("A", "A", "A"));
    assert.equal(order(out), "A,A,A");
    assert.deepEqual(out.map((x: { i?: number }) => x.i), [0, 1, 2]);
  });

  it("emits the trailing burst in order once alternatives run out", () => {
    assert.equal(order(interleavePublishers(P("A", "A", "A", "A", "B"))), "A,B,A,A,A");
  });

  it("is deterministic and keeps first-seen order among equals", () => {
    const items = P("A", "A", "B", "B", "A");
    assert.equal(order(interleavePublishers(items)), order(interleavePublishers(items)));
    // Ties break by position: the FIRST different-publisher item is pulled, not an arbitrary one.
    assert.deepEqual(interleavePublishers(items).map((x: { i?: number }) => x.i), [0, 2, 1, 3, 4]);
  });

  it("handles empties and missing publishers without judging them", () => {
    assert.deepEqual(interleavePublishers([]), []);
    const out = interleavePublishers([{ publisher: undefined }, { publisher: undefined }]);
    assert.equal(out.length, 2);
  });
});
