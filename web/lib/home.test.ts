import { test } from "node:test";
import assert from "node:assert/strict";
import {
  blindspotStories,
  briefingFacts,
  coverageMix,
  groupByTopic,
  latestStories,
  publisherStats,
  trendingTopics,
} from "./home.ts";
import type { Story } from "../types/domain.ts";

/** A minimal but type-complete Story, so the derivations are exercised against the real contract. */
function story(over: Partial<Story> & { id: string }): Story {
  return {
    title: `Story ${over.id}`,
    summary: "",
    topic: "Politics",
    updatedAt: "2026-07-20T10:00:00Z",
    totalCoverage: 3,
    distribution: { left: 1, center: 1, right: 1 },
    coverage: [],
    timeline: [],
    ...over,
  } as Story;
}

test("briefingFacts counts events, distinct publishers, and blind spots", () => {
  const facts = briefingFacts([
    story({ id: "a", publishers: ["Reuters", "BBC"] }),
    story({ id: "b", publishers: ["BBC", "AP"], blindspotSide: "left" }),
  ]);
  assert.equal(facts.storyCount, 2);
  assert.equal(facts.publisherCount, 3); // BBC counted once across stories
  assert.equal(facts.blindspotCount, 1);
});

test("briefingFacts falls back to coverage rows when `publishers` is absent", () => {
  const facts = briefingFacts([
    story({
      id: "a",
      coverage: [
        { publisher: "Le Monde", headline: "h", lean: 0, leanBucket: "center", register: "reporting", emotion: { fear: 0, outrage: 0, analysis: 1, positive: 0, neutral: 0 }, publishedAt: "2026-07-20T09:00:00Z" },
      ],
    }),
  ]);
  assert.equal(facts.publisherCount, 1);
});

test("briefingFacts reports the newest update and is empty-safe", () => {
  const facts = briefingFacts([
    story({ id: "a", latestUpdate: "2026-07-20T08:00:00Z" }),
    story({ id: "b", latestUpdate: "2026-07-21T08:00:00Z" }),
  ]);
  assert.equal(facts.latestUpdate, "2026-07-21T08:00:00Z");

  const empty = briefingFacts([]);
  assert.deepEqual(empty, { storyCount: 0, publisherCount: 0, blindspotCount: 0, latestUpdate: null });
});

test("groupByTopic only promotes topics with enough coverage, and honours exclusions", () => {
  const stories = [
    ...["p1", "p2", "p3"].map((id) => story({ id, topic: "Politics" })),
    ...["b1", "b2"].map((id) => story({ id, topic: "Business" })),
  ];
  const groups = groupByTopic(stories, { minStories: 3 });
  assert.deepEqual(groups.map((g) => g.topic), ["Politics"]); // Business is too thin to earn a module

  // Excluding one Politics story drops it below the threshold entirely.
  assert.deepEqual(groupByTopic(stories, { minStories: 3, exclude: ["p1"] }), []);
});

test("groupByTopic skips unclassified events and caps the number of modules", () => {
  const stories = [
    ...["x1", "x2"].map((id) => story({ id, topic: "" })),
    ...["a1", "a2"].map((id) => story({ id, topic: "Tech" })),
    ...["b1", "b2"].map((id) => story({ id, topic: "Health" })),
  ];
  const groups = groupByTopic(stories, { minStories: 2, maxGroups: 1 });
  assert.equal(groups.length, 1);
  assert.ok(["Tech", "Health"].includes(groups[0].topic));
  assert.ok(!groups.some((g) => g.topic === ""));
});

test("trendingTopics ranks by coverage depth and respects the limit", () => {
  const stories = [
    ...["a", "b", "c"].map((id) => story({ id, topic: "Politics" })),
    ...["d"].map((id) => story({ id, topic: "Tech" })),
  ];
  assert.deepEqual(trendingTopics(stories), [
    { topic: "Politics", count: 3 },
    { topic: "Tech", count: 1 },
  ]);
  assert.equal(trendingTopics(stories, 1).length, 1);
  assert.deepEqual(trendingTopics([]), []);
});

test("blindspotStories keeps only flagged events, widest coverage first", () => {
  const picked = blindspotStories([
    story({ id: "a", totalCoverage: 4 }), // no flag
    story({ id: "b", totalCoverage: 9, blindspotSide: "right" }),
    story({ id: "c", totalCoverage: 12, blindspotSide: "left" }),
  ]);
  assert.deepEqual(picked.map((s) => s.id), ["c", "b"]);
  assert.equal(blindspotStories([]).length, 0);
});

test("latestStories sorts newest first and honours exclusions", () => {
  const stories = [
    story({ id: "old", latestUpdate: "2026-07-18T00:00:00Z" }),
    story({ id: "new", latestUpdate: "2026-07-22T00:00:00Z" }),
    story({ id: "mid", latestUpdate: "2026-07-20T00:00:00Z" }),
  ];
  assert.deepEqual(latestStories(stories).map((s) => s.id), ["new", "mid", "old"]);
  assert.deepEqual(latestStories(stories, 8, ["new"]).map((s) => s.id), ["mid", "old"]);
  assert.equal(latestStories(stories, 1).length, 1);
});

test("latestStories does not mutate its input", () => {
  const stories = [
    story({ id: "old", latestUpdate: "2026-07-18T00:00:00Z" }),
    story({ id: "new", latestUpdate: "2026-07-22T00:00:00Z" }),
  ];
  latestStories(stories);
  assert.deepEqual(stories.map((s) => s.id), ["old", "new"]);
});

test("coverageMix sums per-story distributions and is empty-safe", () => {
  const mix = coverageMix([
    story({ id: "a", distribution: { left: 3, center: 1, right: 2 } }),
    story({ id: "b", distribution: { left: 1, center: 0, right: 4 } }),
  ]);
  assert.deepEqual(mix, { left: 4, center: 1, right: 6 });
  assert.deepEqual(coverageMix([]), { left: 0, center: 0, right: 0 });
});

test("publisherStats counts each publisher once per story", () => {
  const stats = publisherStats([
    story({ id: "a", publishers: ["Reuters", "Reuters", "BBC"] }), // duplicate within one story
    story({ id: "b", publishers: ["Reuters"] }),
  ]);
  assert.deepEqual(stats, [
    { publisher: "Reuters", stories: 2 },
    { publisher: "BBC", stories: 1 },
  ]);
});
