/**
 * Commit 23 — presentation-module tests (node --test, type-stripped like i18n-core.test.ts).
 *
 * The module no longer selects anything: the resolver emits semantic `{key, params}` parts and
 * this layer only maps them to catalog templates. Proves: part→template mapping + params
 * passthrough; the story card keeps its structural payload (caption, comparison, CTA, story
 * link); CTAs stay type-keyed; unknown semantic keys and part-free explanations degrade to the
 * message path (never a raw key on screen); and — the licensing guarantee — every template this
 * module can emit exists in en.json.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { presentRecommendation, hoursAfter } from "@ih/core/logic/rec-presentation";

const HERE = dirname(fileURLToPath(import.meta.url));
const EN = JSON.parse(
  readFileSync(join(HERE, "..", "..", "packages", "core", "i18n", "messages", "en.json"), "utf8"),
) as Record<string, string>;

const STORY_EV = {
  storyId: "s1",
  readUrl: "https://foxnews.example.com/story/1",
  readPublisher: "Fox News",
  recPublisher: "CNN",
  storyReads: 1,
  readPublishedAt: "2026-07-09T08:00:00+00:00",
  recPublishedAt: "2026-07-10T10:00:00+00:00",
};

function storyExp(variant: string, parts: Record<string, unknown> = {}) {
  return {
    type: "story_match", priority: 1, variant, message: "m", evidence: STORY_EV,
    readerFact: { key: "read_story_from", params: { publisher: "Fox News" } },
    contribution: { key: "covered_same_story", params: { publisher: "CNN" } },
    ...parts,
  } as never;
}

test("story_match maps parts to templates AND keeps the structural card payload", () => {
  const p = presentRecommendation(storyExp("same_event"));
  assert.deepEqual(p.reader, { key: "rec.reader.read_story_from", params: { publisher: "Fox News" } });
  assert.deepEqual(p.contribution, { key: "rec.contribution.covered_same_story", params: { publisher: "CNN" } });
  assert.equal(p.claimKey, "rec.claim.story_match.same_event");
  assert.equal(p.ctaKey, "rec.cta.compare");
  assert.equal(p.storyHref, "/stories/s1");
  assert.ok(p.comparison);
  assert.equal(p.comparison!.hoursAfterRead, 26);
});

test("story variants keep their captions and CTAs (Commit 22 layout unchanged)", () => {
  const up = presentRecommendation(storyExp("follow_up", {
    contribution: { key: "story_update", params: { publisher: "CNN" } },
  }));
  assert.equal(up.claimKey, "rec.claim.story_match.follow_up");
  assert.equal(up.ctaKey, "rec.cta.update");
  assert.equal(up.contribution!.key, "rec.contribution.story_update");

  const fo = presentRecommendation(storyExp("following", {
    readerFact: { key: "following_story", params: { n: 2 } },
    contribution: { key: "story_coverage", params: { publisher: "CNN" } },
  }));
  assert.equal(fo.claimKey, "rec.claim.story_match.following");
  assert.equal(fo.ctaKey, "rec.cta.compare");
  assert.deepEqual(fo.reader, { key: "rec.reader.following_story", params: { n: 2 } });
});

test("story_match without a storyId gets no dead link", () => {
  const { storyId: _omit, ...ev } = STORY_EV as Record<string, unknown>;
  const p = presentRecommendation(storyExp("same_event", { evidence: ev }));
  assert.equal(p.storyHref, null);
  assert.ok(p.comparison);
});

test("bridge with a leaning profile renders reader-first; balanced degrades to the message path", () => {
  const leaning = presentRecommendation({
    type: "bridge", priority: 2, message: "m",
    readerFact: { key: "political_lean_right", params: {} },
    contribution: { key: "other_side_perspective", params: {} },
    evidence: { crossCutting: true, articleLean: -1.4, readerPoliticalProfile: "leans_right" },
  } as never);
  assert.deepEqual(leaning.reader, { key: "rec.reader.political_lean_right", params: {} });
  assert.deepEqual(leaning.contribution, { key: "rec.contribution.other_side_perspective", params: {} });
  assert.equal(leaning.ctaKey, "rec.cta.perspective");

  // balanced profile: the resolver emits NO parts — the card falls back to the validated sentence
  const balanced = presentRecommendation({
    type: "bridge", priority: 2, message: "m",
    evidence: { crossCutting: true, articleLean: -1.4, readerPoliticalProfile: "balanced" },
  } as never);
  assert.equal(balanced.reader, null);
  assert.equal(balanced.contribution, null);
  assert.equal(balanced.ctaKey, "rec.cta.perspective"); // CTA stays type-keyed
});

test("new_publisher parts pass params through (never vs rarely)", () => {
  const never = presentRecommendation({
    type: "new_publisher", priority: 3, message: "m",
    readerFact: { key: "never_read_publisher", params: { publisher: "The Hill" } },
    contribution: { key: "add_new_publisher", params: {} },
    evidence: { band: "never", reads: 0 },
  } as never);
  assert.deepEqual(never.reader, { key: "rec.reader.never_read_publisher", params: { publisher: "The Hill" } });
  assert.equal(never.ctaKey, "rec.cta.explore");

  const rarely = presentRecommendation({
    type: "new_publisher", priority: 3, message: "m",
    readerFact: { key: "rarely_read_publisher", params: { publisher: "The Hill", n: 2 } },
    contribution: { key: "add_new_publisher", params: {} },
    evidence: { band: "rarely", reads: 2 },
  } as never);
  assert.deepEqual(rarely.reader!.params, { publisher: "The Hill", n: 2 });
});

test("C6 share-backed reader facts map to their concrete templates with params intact", () => {
  const topicShare = presentRecommendation({
    type: "topic_continuity", priority: 4, message: "m",
    readerFact: { key: "top_topic_share", params: { topic: "Politics", percent: 42 } },
    contribution: { key: "more_topic_coverage", params: {} },
    evidence: { topic: "Politics", topicShare: 0.42 },
  } as never);
  assert.deepEqual(topicShare.reader,
    { key: "rec.reader.top_topic_share", params: { topic: "Politics", percent: 42 } });

  const leanShare = presentRecommendation({
    type: "bridge", priority: 2, message: "m",
    readerFact: { key: "political_lean_left_share", params: { percent: 74 } },
    contribution: { key: "other_side_perspective", params: {} },
    evidence: { crossCutting: true, readerPoliticalProfile: "leans_left",
                readerLeanShares: { left: 0.74, center: 0.16, right: 0.1 } },
  } as never);
  assert.deepEqual(leanShare.reader,
    { key: "rec.reader.political_lean_left_share", params: { percent: 74 } });
  assert.equal(leanShare.ctaKey, "rec.cta.perspective");
});

test("topic renders reader-first; long_tail is the contribution-first exception", () => {
  const topic = presentRecommendation({
    type: "topic_continuity", priority: 4, message: "m",
    readerFact: { key: "top_topic", params: { topic: "Politics" } },
    contribution: { key: "more_topic_coverage", params: {} },
    evidence: { topic: "Politics" },
  } as never);
  assert.deepEqual(topic.reader, { key: "rec.reader.top_topic", params: { topic: "Politics" } });
  assert.equal(topic.ctaKey, null);

  const tail = presentRecommendation({
    type: "long_tail", priority: 5, message: "m",
    contribution: { key: "rare_in_feeds", params: {} },
    evidence: { strategy: "rwe-d" },
  } as never);
  assert.equal(tail.reader, null); // no reader fact exists — documented exception
  assert.deepEqual(tail.contribution, { key: "rec.contribution.rare_in_feeds", params: {} });
});

test("part-free and unknown explanations degrade to the message path", () => {
  for (const exp of [
    { type: "coverage_breadth", priority: 6, message: "m", evidence: { topic: "Business" } },
    { type: "mystery", priority: 9, message: "m" },
    undefined,
    null,
  ] as const) {
    const p = presentRecommendation(exp as never);
    assert.equal(p.reader, null);
    assert.equal(p.contribution, null);
    assert.equal(p.comparison, null);
  }
});

test("an unknown semantic part key never renders raw — it degrades to the message path", () => {
  const p = presentRecommendation({
    type: "bridge", priority: 2, message: "m",
    readerFact: { key: "a_future_fact_this_build_does_not_know", params: {} },
    evidence: { crossCutting: true, readerPoliticalProfile: "leans_left" },
  } as never);
  assert.equal(p.reader, null);
});

test("hoursAfter is defensive display arithmetic", () => {
  assert.equal(hoursAfter("2026-07-10T10:00:00+00:00", "2026-07-09T08:00:00+00:00"), 26);
  assert.equal(hoursAfter("garbage", "2026-07-09T08:00:00+00:00"), null);
  assert.equal(hoursAfter(undefined, "2026-07-09T08:00:00+00:00"), null);
});

test("every template this module can emit exists in the English catalog (licensing guarantee)", () => {
  const emitted = new Set<string>();
  const readerKeys = ["read_story_from", "following_story", "never_read_publisher",
                      "rarely_read_publisher", "top_topic", "political_lean_left", "political_lean_right"];
  const contribKeys = ["covered_same_story", "story_update", "story_coverage", "add_new_publisher",
                       "more_topic_coverage", "other_side_perspective", "rare_in_feeds"];
  for (const key of readerKeys) {
    const p = presentRecommendation({ type: "bridge", priority: 2, message: "m",
      readerFact: { key, params: {} }, evidence: {} } as never);
    if (p.reader) emitted.add(p.reader.key);
  }
  for (const key of contribKeys) {
    const p = presentRecommendation({ type: "bridge", priority: 2, message: "m",
      contribution: { key, params: {} }, evidence: {} } as never);
    if (p.contribution) emitted.add(p.contribution.key);
  }
  for (const variant of ["same_event", "follow_up", "following"]) {
    const p = presentRecommendation(storyExp(variant));
    if (p.claimKey) emitted.add(p.claimKey);
    if (p.ctaKey) emitted.add(p.ctaKey);
  }
  emitted.add("rec.cta.perspective");
  emitted.add("rec.cta.explore");
  assert.equal(emitted.size, 14 + 3 + 2 + 2); // 14 part templates + 3 captions + 4 CTAs
  for (const key of emitted) {
    assert.ok(EN[key] !== undefined && EN[key].trim() !== "", `missing catalog key: ${key}`);
  }
});
