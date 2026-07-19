import { test } from "node:test";
import assert from "node:assert/strict";
import { notificationPresentation, badgeLabel } from "./notifications.ts";

const KNOWN = [
  "weekly_report",
  "monthly_deep_dive",
  "recommendations_waiting",
  "weekly_digest",
  "streak_reminder",
  "blind_spot_alert",
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

test("badgeLabel caps: 0 hidden, 1-9 numeric, >9 -> 9+", () => {
  assert.equal(badgeLabel(0), "");
  assert.equal(badgeLabel(-3), "");
  assert.equal(badgeLabel(1), "1");
  assert.equal(badgeLabel(9), "9");
  assert.equal(badgeLabel(10), "9+");
  assert.equal(badgeLabel(250), "9+");
});
