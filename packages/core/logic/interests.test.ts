import { test } from "node:test";
import assert from "node:assert/strict";
import type { InterestIntensity } from "../domain/types.ts";
import {
  INTEREST_FOLLOWED,
  INTEREST_KEYS,
  INTEREST_NEUTRAL,
  interestForTopic,
  isFollowedInterest,
  toggleInterest,
} from "./interests.ts";

const neutral = (): InterestIntensity =>
  Object.fromEntries(INTEREST_KEYS.map((k) => [k, INTEREST_NEUTRAL])) as unknown as InterestIntensity;

test("a catalog topic maps to the interest slider that can actually nudge it", () => {
  assert.equal(interestForTopic("Business"), "business");
  assert.equal(interestForTopic("Technology"), "technology");
  assert.equal(interestForTopic("Arts"), "artsCulture");
  // Case and punctuation are catalog noise, not meaning.
  assert.equal(interestForTopic("ARTS & CULTURE"), "artsCulture");
  assert.equal(interestForTopic("arts-culture"), "artsCulture");
  assert.equal(interestForTopic("Environment"), "climate");
});

test("a topic with no slider behind it maps to nothing, so the UI can omit the control", () => {
  // The whole point: Politics and World are real catalog topics with NO interest key. A follow
  // button on them would write nothing and change nothing — worse than its absence.
  for (const topic of ["Politics", "World", "Lake Ontario", ""]) {
    assert.equal(interestForTopic(topic), null, `${topic} has no interest slider`);
  }
  assert.equal(interestForTopic(null), null);
  assert.equal(interestForTopic(undefined), null);
});

test("following is a boost above neutral; unfollowing returns to neutral, never below", () => {
  const base = neutral();
  assert.equal(isFollowedInterest(base, "sports"), false);

  const followed = toggleInterest(base, "sports");
  assert.equal(followed.sports, INTEREST_FOLLOWED);
  assert.equal(isFollowedInterest(followed, "sports"), true);

  const unfollowed = toggleInterest(followed, "sports");
  assert.equal(
    unfollowed.sports,
    INTEREST_NEUTRAL,
    "unfollowing restores the untouched feed — it does not suppress the topic",
  );
});

test("toggling one interest leaves the other seven exactly as the reader set them", () => {
  const tuned: InterestIntensity = { ...neutral(), science: 9, health: 2 };
  const next = toggleInterest(tuned, "sports");
  assert.equal(next.science, 9);
  assert.equal(next.health, 2);
  assert.notEqual(next, tuned, "returns a new object rather than mutating settings in place");
});

test("a slider the reader raised by hand already reads as followed", () => {
  // Settings and this control are two views of ONE value, so a 7 set on the slider must show as
  // followed here — otherwise the two surfaces would disagree about the same number.
  assert.equal(isFollowedInterest({ ...neutral(), climate: 7 }, "climate"), true);
  assert.equal(isFollowedInterest({ ...neutral(), climate: 3 }, "climate"), false);
  assert.equal(isFollowedInterest(undefined, "climate"), false);
});
