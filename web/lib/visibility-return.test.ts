// The Story Continuation trigger's dwell gate (design §2.1) — the rule tested WITHOUT React, so it
// can be asserted at the millisecond rather than through a renderer.
//
// `hooks/use-visibility-return.ts` CALLS this same `createDwellGate` — it only wires the browser
// event to it — so these assertions are about the shipped rule rather than a copy of it. An earlier
// draft inlined the state machine in both places, which would have let the hook drift while the
// tests stayed green.
//
// Two decisions, both with a concrete failure mode: firing below the threshold is "a strip after a
// 4 s alt-tab", and firing without a preceding hide is "a strip on bfcache restore, without the
// reader having gone anywhere".
import { test } from "node:test";
import assert from "node:assert/strict";
import { createDwellGate as makeGate } from "./continuation.ts";

test("a 21 s absence fires; a 19 s absence does not", () => {
  const fired: number[] = [];
  const gate = makeGate(20_000, (ms) => fired.push(ms));

  gate("hidden", 0);
  gate("visible", 19_000);
  assert.deepEqual(fired, [], "19 s is an alt-tab, not a read");

  gate("hidden", 100_000);
  gate("visible", 121_000);
  assert.deepEqual(fired, [21_000], "21 s is a return, and the dwell is reported");
});

test("exactly minHiddenMs fires — the boundary is inclusive", () => {
  const fired: number[] = [];
  const gate = makeGate(20_000, (ms) => fired.push(ms));
  gate("hidden", 0);
  gate("visible", 20_000);
  assert.deepEqual(fired, [20_000]);
});

test("a visible event with no preceding hide never fires", () => {
  // The tab was already visible at mount, or the browser fired visibilitychange on a bfcache
  // restore. No hide means no dwell was measured, and an unmeasured dwell is not a return.
  const fired: number[] = [];
  const gate = makeGate(20_000, (ms) => fired.push(ms));
  gate("visible", 50_000);
  gate("visible", 90_000);
  assert.deepEqual(fired, []);
});

test("each qualifying return is one event — repeats are not suppressed here", () => {
  // Suppressing a second offer is the impression cap's job (lib/continuation.mayShow), not the
  // trigger's. Conflating them would make the cap untestable and the trigger stateful about
  // something it does not own.
  const fired: number[] = [];
  const gate = makeGate(20_000, (ms) => fired.push(ms));
  gate("hidden", 0);
  gate("visible", 30_000);
  gate("hidden", 40_000);
  gate("visible", 80_000);
  assert.deepEqual(fired, [30_000, 40_000]);
});

test("a second hide before any return replaces the first — the dwell is from the LATEST hide", () => {
  const fired: number[] = [];
  const gate = makeGate(20_000, (ms) => fired.push(ms));
  gate("hidden", 0);
  gate("hidden", 100_000); // e.g. a duplicate event; the reader is still away
  gate("visible", 110_000);
  assert.deepEqual(fired, [], "10 s since the latest hide is below the gate");
});
