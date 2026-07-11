/**
 * Commit 22 — presentation-module tests (node --test, type-stripped like i18n-core.test.ts).
 *
 * Proves: every explanation type/variant maps to the right claim/receipt/CTA keys; the story
 * comparison payload is faithful to evidence (incl. the hours arithmetic and the story deep
 * link); claim-free and unknown types fall back to nulls (the card then renders the resolver's
 * validated sentence); and — the licensing guarantee — every catalog key this module can emit
 * exists in en.json, so a shown claim can never be a missing-key fallback.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { presentRecommendation, hoursAfter } from "./rec-presentation.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const EN = JSON.parse(
  readFileSync(join(HERE, "..", "messages", "en.json"), "utf8"),
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

test("story_match/same_event → compare claim, compare CTA, comparison payload, story link", () => {
  const p = presentRecommendation({
    type: "story_match", priority: 1, variant: "same_event", message: "m", evidence: STORY_EV,
  });
  assert.equal(p.claimKey, "rec.claim.story_match.same_event");
  assert.equal(p.ctaKey, "rec.cta.compare");
  assert.equal(p.storyHref, "/stories/s1");
  assert.ok(p.comparison);
  assert.equal(p.comparison!.readPublisher, "Fox News");
  assert.equal(p.comparison!.recPublisher, "CNN");
  assert.equal(p.comparison!.hoursAfterRead, 26); // display arithmetic on gated timestamps
});

test("story_match/follow_up → update claim + update CTA", () => {
  const p = presentRecommendation({
    type: "story_match", priority: 1, variant: "follow_up", message: "m", evidence: STORY_EV,
  });
  assert.equal(p.claimKey, "rec.claim.story_match.follow_up");
  assert.equal(p.ctaKey, "rec.cta.update");
  assert.equal(p.comparison!.variant, "follow_up");
});

test("story_match/following keeps the compare CTA and carries storyReads", () => {
  const p = presentRecommendation({
    type: "story_match", priority: 1, variant: "following", message: "m",
    evidence: { ...STORY_EV, storyReads: 2 },
  });
  assert.equal(p.claimKey, "rec.claim.story_match.following");
  assert.equal(p.ctaKey, "rec.cta.compare");
  assert.equal(p.comparison!.storyReads, 2);
});

test("story_match without a storyId gets no dead link", () => {
  const { storyId: _omit, ...ev } = STORY_EV as Record<string, unknown>;
  const p = presentRecommendation({ type: "story_match", priority: 1, message: "m", evidence: ev });
  assert.equal(p.storyHref, null);
  assert.ok(p.comparison); // the comparison itself is still licensed by publishers/urls
});

test("bridge → balance claim, cross-cutting receipt, perspective CTA", () => {
  const p = presentRecommendation({
    type: "bridge", priority: 2, message: "m", evidence: { crossCutting: true, articleLean: 1.4 },
  });
  assert.equal(p.claimKey, "rec.claim.bridge");
  assert.deepEqual(p.receipts.map((r) => r.key), ["rec.receipt.crossCutting"]);
  assert.equal(p.ctaKey, "rec.cta.perspective");
  assert.equal(p.comparison, null);
});

test("new_publisher differentiates never vs rarely in the receipt", () => {
  const never = presentRecommendation({
    type: "new_publisher", priority: 3, message: "m",
    evidence: { publisher: "The Hill", reads: 0, share: 0, band: "never" },
  });
  assert.equal(never.claimKey, "rec.claim.new_publisher");
  assert.equal(never.ctaKey, "rec.cta.explore");
  assert.deepEqual(never.receipts, [{ key: "rec.receipt.publisherNever", params: { publisher: "The Hill" } }]);

  const rarely = presentRecommendation({
    type: "new_publisher", priority: 3, message: "m",
    evidence: { publisher: "The Hill", reads: 2, share: 0.02, band: "rarely" },
  });
  assert.deepEqual(rarely.receipts, [
    { key: "rec.receipt.publisherRarely", params: { publisher: "The Hill", n: 2 } },
  ]);
});

test("topic_continuity and long_tail claim but keep the default CTA", () => {
  const topic = presentRecommendation({
    type: "topic_continuity", priority: 4, message: "m", evidence: { topic: "Politics" },
  });
  assert.equal(topic.claimKey, "rec.claim.topic_continuity");
  assert.deepEqual(topic.receipts, [{ key: "rec.receipt.topTopic", params: { topic: "Politics" } }]);
  assert.equal(topic.ctaKey, null);

  const tail = presentRecommendation({ type: "long_tail", priority: 5, message: "m", evidence: {} });
  assert.equal(tail.claimKey, "rec.claim.long_tail");
  assert.equal(tail.ctaKey, null);
});

test("coverage_breadth, unknown types, and missing explanations fall back to the sentence path", () => {
  for (const exp of [
    { type: "coverage_breadth", priority: 6, message: "m", evidence: { topic: "Business" } },
    { type: "mystery", priority: 9, message: "m" },
    undefined,
    null,
  ] as const) {
    const p = presentRecommendation(exp as never);
    assert.equal(p.claimKey, null);
    assert.equal(p.ctaKey, null);
    assert.deepEqual(p.receipts, []);
    assert.equal(p.comparison, null);
  }
});

test("hoursAfter is defensive display arithmetic", () => {
  assert.equal(hoursAfter("2026-07-10T10:00:00+00:00", "2026-07-09T08:00:00+00:00"), 26);
  assert.equal(hoursAfter("garbage", "2026-07-09T08:00:00+00:00"), null);
  assert.equal(hoursAfter(undefined, "2026-07-09T08:00:00+00:00"), null);
  assert.equal(hoursAfter("", ""), null);
});

test("every key the module can emit exists in the English catalog (licensing guarantee)", () => {
  const emitted = new Set<string>();
  const cases = [
    { type: "story_match", variant: "same_event" }, { type: "story_match", variant: "follow_up" },
    { type: "story_match", variant: "following" }, { type: "bridge" },
    { type: "new_publisher", evidence: { band: "never" } },
    { type: "new_publisher", evidence: { band: "rarely" } },
    { type: "topic_continuity" }, { type: "long_tail" },
  ];
  for (const c of cases) {
    const p = presentRecommendation({ priority: 1, message: "m", evidence: {}, ...c } as never);
    if (p.claimKey) emitted.add(p.claimKey);
    if (p.ctaKey) emitted.add(p.ctaKey);
    for (const r of p.receipts) emitted.add(r.key);
  }
  assert.ok(emitted.size >= 10, "expected a substantial key surface");
  for (const key of emitted) {
    assert.ok(EN[key] !== undefined && EN[key].trim() !== "", `missing catalog key: ${key}`);
  }
});
