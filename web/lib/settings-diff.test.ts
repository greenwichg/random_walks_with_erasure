import { test } from "node:test";
import assert from "node:assert/strict";
import { diffSettings, hasChanges } from "./settings-diff.ts";
import type { Settings } from "../types/domain.ts";

/** A complete baseline Settings object (the S1.2 contract — no `privacy` group). */
function base(): Settings {
  return {
    theme: "system",
    language: "en",
    politicalOpenness: 50,
    recommendationStrength: 50,
    readingGoalMinutes: 20,
    weeklyReport: true,
    monthlyReport: false,
    notifications: {
      recommendations: true,
      weeklyDigest: true,
      streakReminders: false,
      blindSpotAlerts: false,
    },
  };
}

test("identical objects → empty diff (and hasChanges is false)", () => {
  const b = base();
  const patch = diffSettings(b, base());
  assert.deepEqual(patch, {});
  assert.equal(hasChanges(patch), false);
});

test("a primitive field change → only that field", () => {
  const patch = diffSettings(base(), { ...base(), readingGoalMinutes: 45 });
  assert.deepEqual(patch, { readingGoalMinutes: 45 });
  assert.equal(hasChanges(patch), true);
});

test("a nested object change → only the changed sub-field (not the whole group)", () => {
  const draft = base();
  draft.notifications = { ...draft.notifications, blindSpotAlerts: true };
  assert.deepEqual(diffSettings(base(), draft), { notifications: { blindSpotAlerts: true } });
});

test("multiple nested changes → each changed sub-field, unchanged ones omitted", () => {
  const draft = base();
  draft.notifications = { ...draft.notifications, weeklyDigest: false, streakReminders: true };
  assert.deepEqual(diffSettings(base(), draft), {
    notifications: { weeklyDigest: false, streakReminders: true },
  });
});

test("unchanged nested object → the group is absent from the diff", () => {
  const draft = base();
  draft.readingGoalMinutes = 30; // change a sibling; notifications is untouched
  const patch = diffSettings(base(), draft);
  assert.deepEqual(patch, { readingGoalMinutes: 30 });
  assert.ok(!("notifications" in patch));
});

test("a top-level change and a nested change combine", () => {
  const draft = base();
  draft.politicalOpenness = 80;
  draft.notifications = { ...draft.notifications, recommendations: false };
  assert.deepEqual(diffSettings(base(), draft), {
    politicalOpenness: 80,
    notifications: { recommendations: false },
  });
});

test("pure: neither input is mutated", () => {
  const b = base();
  const d = base();
  d.weeklyReport = false;
  const bSnapshot = JSON.stringify(b);
  const dSnapshot = JSON.stringify(d);
  diffSettings(b, d);
  assert.equal(JSON.stringify(b), bSnapshot);
  assert.equal(JSON.stringify(d), dSnapshot);
});

test("deterministic: identical inputs → deep-equal output", () => {
  const draft = { ...base(), monthlyReport: true };
  assert.deepEqual(diffSettings(base(), draft), diffSettings(base(), draft));
});

test("arrays diff by value: identical rebuilt array is no change; a changed array ships whole", () => {
  const withUs = { ...base(), locations: [{ placeId: "US", level: "country" }] } as never;
  const same = { ...base(), locations: [{ placeId: "US", level: "country" }] } as never;
  const changed = { ...base(), locations: [{ placeId: "GB", level: "country" }] } as never;
  assert.deepEqual(diffSettings(withUs, same), {});
  assert.deepEqual(diffSettings(withUs, changed), { locations: [{ placeId: "GB", level: "country" }] });
});
