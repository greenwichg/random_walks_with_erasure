import { test } from "node:test";
import assert from "node:assert/strict";
import { notificationPresentation, notificationHref, badgeLabel } from "./notifications.ts";

const KNOWN = [
  "weekly_report",
  "monthly_deep_dive",
  "recommendations_waiting",
  "weekly_digest",
  "streak_reminder",
  "blind_spot_alert",
  "breaking_story",
];

test("every known kind maps to a distinct title key + an icon", () => {
  const titles = new Set<string>();
  for (const kind of KNOWN) {
    const p = notificationPresentation(kind);
    assert.ok(p.icon, `${kind} has an icon`);
    assert.match(p.titleKey, /^notifications\..+\.title$/, `${kind} title key shape`);
    titles.add(p.titleKey);
  }
  assert.equal(titles.size, KNOWN.length, "title keys are distinct");
});

test("an unknown kind degrades to the safe generic row (never crashes, never a raw key)", () => {
  const p = notificationPresentation("some_future_kind_we_do_not_know");
  assert.equal(p.titleKey, "notifications.generic.title");
  assert.equal(p.bodyKey, null);
  assert.equal(p.href, null);
  assert.ok(p.icon, "generic row still has an icon");
});

// ---------------------------------------------------------------------------------------------
// notificationHref — the payload-aware destination. Everything above resolves a kind; this
// resolves ONE notification, because breaking stories are the first kind whose destination is
// per-row rather than per-kind.
// ---------------------------------------------------------------------------------------------

test("breaking_story deep-links to the story its payload names", () => {
  assert.equal(
    notificationHref("breaking_story", { storyId: "s-42", title: "Something happened" }),
    "/stories/s-42",
  );
});

test("breaking_story falls back to the stories index when the payload has no usable storyId", () => {
  // Stored JSON outlives the shape that wrote it: a row from before the payload carried a storyId,
  // or one whose producer changed, must still navigate somewhere real — never /stories/undefined.
  for (const payload of [
    undefined,
    {},
    { storyId: null },
    { storyId: "" },
    { storyId: "   " },
    { storyId: 42 },
    { storyId: { id: "s-1" } },
  ]) {
    assert.equal(notificationHref("breaking_story", payload), "/stories");
  }
});

test("breaking_story escapes the story id (a payload is data, never a path fragment)", () => {
  assert.equal(notificationHref("breaking_story", { storyId: "a/b?c#d" }), "/stories/a%2Fb%3Fc%23d");
  assert.equal(notificationHref("breaking_story", { storyId: "  s-7  " }), "/stories/s-7");
});

test("every other kind ignores the payload and keeps its static destination", () => {
  for (const kind of KNOWN) {
    if (kind === "breaking_story") continue;
    const { href } = notificationPresentation(kind);
    assert.equal(notificationHref(kind, { storyId: "s-42" }), href, `${kind} is unaffected`);
    assert.equal(notificationHref(kind), href, `${kind} without a payload`);
  }
});

test("an unknown kind has no destination even with a story-shaped payload", () => {
  assert.equal(notificationHref("some_future_kind", { storyId: "s-42" }), null);
});

test("badgeLabel caps: 0 hidden, 1-9 numeric, >9 -> 9+", () => {
  assert.equal(badgeLabel(0), "");
  assert.equal(badgeLabel(-3), "");
  assert.equal(badgeLabel(1), "1");
  assert.equal(badgeLabel(9), "9");
  assert.equal(badgeLabel(10), "9+");
  assert.equal(badgeLabel(250), "9+");
});
