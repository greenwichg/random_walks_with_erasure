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
import type { Story } from "../domain/types.ts";

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

// ---------------------------------------------------------------------------------------------
// Topic views (HOME2) — completing a thin category without inventing content.
//
// Mutation ledger (each check went red against the listed break of home.ts):
//  - mergeStories without the seen-set                -> "one event never twice" fails
//  - mergeStories extras first                        -> "page order first" fails
//  - urlKey keeps the query string                    -> "utm variant is the same article" fails
//  - freshArticles admits undated rows               -> "unknown age is not fresh" fails
//  - freshArticles skips the exclude set              -> "a story's own coverage is not new" fails
//  - per-publisher cap removed                        -> "one wire cannot fill the slots" fails
//  - withTopicCount adds a missing chip               -> "never adds a chip" fails
//  - isoHoursAgo not truncated to the hour            -> "stable within the hour" fails
// ---------------------------------------------------------------------------------------------
import {
  coverageUrlKeys,
  freshArticles,
  isoHoursAgo,
  mergeStories,
  topicTier,
  urlKey,
  withTopicCount,
  TOPIC_SPARSE_EVENTS,
  TOPIC_TARGET_EVENTS,
} from "./home.ts";
import type { Article } from "../domain/types.ts";

const NOW = "2026-09-02T12:00:00Z";

function article(over: Partial<Article> & { id: string }): Article {
  return {
    headline: `Article ${over.id}`,
    publisher: "Wire",
    topic: "Technology",
    url: `https://example.com/${over.id}`,
    publishedAt: "2026-09-02T11:00:00Z",
    readingMinutes: 3,
    ...over,
  } as Article;
}

test("topicTier: sparse at the floor, thin below the target, full at it", () => {
  assert.equal(topicTier(0), "sparse");
  assert.equal(topicTier(TOPIC_SPARSE_EVENTS), "sparse");
  assert.equal(topicTier(TOPIC_SPARSE_EVENTS + 1), "thin");
  assert.equal(topicTier(TOPIC_TARGET_EVENTS - 1), "thin");
  assert.equal(topicTier(TOPIC_TARGET_EVENTS), "full");
});

test("mergeStories: page order first, top-up appended, one event never twice", () => {
  const merged = mergeStories(
    [story({ id: "a" }), story({ id: "b" })],
    [story({ id: "b", title: "duplicate" }), story({ id: "c" }), story({ id: "a" })],
  );
  assert.deepEqual(merged.map((s) => s.id), ["a", "b", "c"]);
  assert.equal(merged[1].title, "Story b", "the page's copy wins over the top-up's");
});

test("urlKey: a utm variant, a scheme change and a trailing slash are the same article", () => {
  const k = urlKey("https://www.example.com/news/story/");
  assert.equal(k, "example.com/news/story");
  assert.equal(urlKey("http://example.com/news/story?utm_source=rss#top"), k);
  assert.equal(urlKey(undefined), "");
});

test("freshArticles: unknown age is not fresh, and neither is anything past the limit", () => {
  const picked = freshArticles(
    [
      article({ id: "fresh", publishedAt: "2026-09-02T10:00:00Z" }),
      article({ id: "undated", publishedAt: "" }),
      article({ id: "old", publishedAt: "2026-08-29T10:00:00Z" }),
      article({ id: "future", publishedAt: "2026-09-03T10:00:00Z" }),
    ],
    { now: NOW, maxAgeHours: 72 },
  );
  assert.deepEqual(picked.map((a) => a.id), ["fresh"]);
});

test("freshArticles: a story's own coverage is not new, and the same URL is not listed twice", () => {
  const shown = [story({
    id: "s",
    coverage: [{ publisher: "Wire", url: "http://www.example.com/covered?x=1", headline: "h",
                 publishedAt: NOW }] as Story["coverage"],
  })];
  const picked = freshArticles(
    [
      article({ id: "covered", url: "https://example.com/covered/" }),
      article({ id: "new", url: "https://example.com/new" }),
      article({ id: "new-again", url: "https://example.com/new?utm=1", publisher: "Other" }),
    ],
    { now: NOW, exclude: coverageUrlKeys(shown) },
  );
  assert.deepEqual(picked.map((a) => a.id), ["new"]);
});

test("freshArticles: newest first, one wire cannot fill the slots, and the limit holds", () => {
  const rows = [
    article({ id: "w1", publisher: "Wire", publishedAt: "2026-09-02T11:00:00Z" }),
    article({ id: "w2", publisher: "Wire", publishedAt: "2026-09-02T10:00:00Z" }),
    article({ id: "w3", publisher: "Wire", publishedAt: "2026-09-02T09:00:00Z" }),
    article({ id: "o1", publisher: "Other", publishedAt: "2026-09-02T08:00:00Z" }),
    article({ id: "t1", publisher: "Third", publishedAt: "2026-09-02T07:00:00Z" }),
  ];
  const picked = freshArticles(rows, { now: NOW, perPublisher: 2, limit: 3 });
  assert.deepEqual(picked.map((a) => a.id), ["w1", "w2", "o1"]);
});

test("withTopicCount: the active chip shows what the view shows; nothing else moves", () => {
  const rail = [{ topic: "Politics", count: 17 }, { topic: "Technology", count: 1 }];
  assert.deepEqual(withTopicCount(rail, "Technology", 9), [
    { topic: "Politics", count: 17 }, { topic: "Technology", count: 9 },
  ]);
  assert.deepEqual(withTopicCount(rail, "Technology", 0), rail, "a count never goes DOWN");
  assert.deepEqual(withTopicCount(rail, "Science", 4), rail, "never adds a chip");
  assert.deepEqual(withTopicCount(rail, null, 4), rail);
});

test("isoHoursAgo: stable within the hour, so the query key does not churn per render", () => {
  const a = isoHoursAgo(72, new Date("2026-09-02T12:05:00Z"));
  const b = isoHoursAgo(72, new Date("2026-09-02T12:55:59Z"));
  assert.equal(a, b);
  assert.equal(a, "2026-08-30T12:00:00.000Z");
});
