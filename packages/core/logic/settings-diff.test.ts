import { test } from "node:test";
import assert from "node:assert/strict";
import { diffSettings, hasChanges } from "./settings-diff.ts";
import type { Settings } from "../domain/types.ts";

/** A complete baseline Settings object (the S1.2 contract — no `privacy` group). */
function base(): Settings {
  return {
    theme: "system",
    language: "en",
    politicalOpenness: 50,
    recommendationStrength: 50,
    recommendationCountry: null,
    interests: {
      business: 5,
      technology: 5,
      science: 5,
      health: 5,
      climate: 5,
      sports: 5,
      entertainment: 5,
      artsCulture: 5,
    },
    readingGoalMinutes: 20,
    weeklyReport: true,
    monthlyReport: false,
    notifications: {
      recommendations: true,
      weeklyDigest: true,
      streakReminders: false,
      blindSpotAlerts: false,
      categories: {
        breaking: { inApp: true, push: false },
        digests: { inApp: true, push: false },
        recommendations: { inApp: true, push: false },
        product: { inApp: true, push: false },
      },
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

test("one moved interest slider → exactly that leaf, never the other seven", () => {
  const draft = base();
  draft.interests = { ...draft.interests, sports: 9 };
  assert.deepEqual(diffSettings(base(), draft), { interests: { sports: 9 } });
});

test("interests reset-to-defaults from an all-default base → empty diff (nothing to save)", () => {
  const draft = base();
  draft.interests = { ...draft.interests }; // a rebuilt identical group is not a change
  assert.deepEqual(diffSettings(base(), draft), {});
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


// --------------------------------------------------------------------------------------------
// Two-level nesting (`notifications.categories.<category>.<channel>`). Minimality matters more
// here than in a flat group: the engine deep-merges, so a patch that restated unchanged siblings
// would overwrite a change another device made between this page's load and its save.
// --------------------------------------------------------------------------------------------
test("a change two levels deep → only that leaf, not the whole matrix", () => {
  const draft = base();
  draft.notifications = {
    ...draft.notifications,
    categories: {
      ...draft.notifications.categories,
      breaking: { ...draft.notifications.categories.breaking, inApp: false },
    },
  };
  assert.deepEqual(diffSettings(base(), draft), {
    notifications: { categories: { breaking: { inApp: false } } },
  });
});

test("the untouched channel of a changed category stays out of the patch", () => {
  const draft = base();
  draft.notifications = {
    ...draft.notifications,
    categories: {
      ...draft.notifications.categories,
      breaking: { inApp: true, push: true }, // only `push` differs from the base
    },
  };
  const patch = diffSettings(base(), draft) as Record<string, any>;
  assert.deepEqual(patch.notifications.categories.breaking, { push: true });
});

test("a rebuilt but identical matrix is not a change", () => {
  const draft = base();
  draft.notifications = {
    ...draft.notifications,
    categories: { ...draft.notifications.categories, breaking: { inApp: true, push: false } },
  };
  assert.deepEqual(diffSettings(base(), draft), {});
});

test("a flat sibling and a deep leaf combine without disturbing each other", () => {
  const draft = base();
  draft.notifications = {
    ...draft.notifications,
    weeklyDigest: false,
    categories: {
      ...draft.notifications.categories,
      product: { ...draft.notifications.categories.product, inApp: false },
    },
  };
  assert.deepEqual(diffSettings(base(), draft), {
    notifications: { weeklyDigest: false, categories: { product: { inApp: false } } },
  });
});

test("resetting the For You country to Global → an explicit null, never an omitted key", () => {
  // The reset path end to end: the PATCH must carry `null`, or the stored country survives the
  // reset (examples/api_fastapi.py re-admits explicitly-sent nulls for exactly this field).
  const b = { ...base(), recommendationCountry: "IN" };
  const patch = diffSettings(b, { ...b, recommendationCountry: null });
  assert.ok("recommendationCountry" in patch);
  assert.equal(patch.recommendationCountry, null);
  assert.equal(hasChanges(patch), true);
});

test("selecting a For You country → exactly that field; an unchanged Global → nothing", () => {
  const b = base();
  assert.deepEqual(diffSettings(b, { ...b, recommendationCountry: "JP" }),
                   { recommendationCountry: "JP" });
  assert.deepEqual(diffSettings(b, { ...b, recommendationCountry: null }), {});
});
