/**
 * Dashboard hero-copy mapping tests (node --test, type-stripped). Locks the core guarantee: only the
 * "Healthy" band maps to the positive headline; every other or unknown band (incl. the "Needs work"
 * band that scoreBand(36) returns) maps to a non-positive message.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { heroCopyKeys } from "./hero-copy.ts";

test("each band maps to its own headline + body keys", () => {
  assert.deepEqual(heroCopyKeys("Healthy"), {
    title: "dashboard.hero.healthy.title",
    body: "dashboard.hero.healthy.body",
  });
  assert.deepEqual(heroCopyKeys("Fair"), {
    title: "dashboard.hero.fair.title",
    body: "dashboard.hero.fair.body",
  });
  assert.deepEqual(heroCopyKeys("Needs work"), {
    title: "dashboard.hero.needsWork.title",
    body: "dashboard.hero.needsWork.body",
  });
});

test("only the Healthy band gets the positive headline (low/unknown never do)", () => {
  assert.equal(heroCopyKeys("Healthy").title, "dashboard.hero.healthy.title");
  for (const band of ["Needs work", "Fair", "Poor", "Unknown", ""]) {
    assert.notEqual(heroCopyKeys(band).title, "dashboard.hero.healthy.title");
  }
});
