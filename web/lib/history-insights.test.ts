/**
 * Phase 1 — history-insights tests (node --test, type-stripped like the other lib tests).
 * Proves the aggregation (counts, shares, top-N, concentration, averages) and the softly-thresholded
 * classifiers used by the Reflection/Insights copy, including the empty-set degradation.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  summarizeHistory,
  classifyTilt,
  classifyBreadth,
  classifyReporting,
  tally,
  dayKey,
  sessionize,
  timeBucket,
  readingPattern,
} from "./history-insights.ts";

type Art = { topic: string; publisher: string; lean: number; register: string; readingMinutes: number;
             emotion?: Record<string, number> };
const NEUTRAL = { fear: 0, outrage: 0, analysis: 0, positive: 0, neutral: 1 };
const entry = (a: Art, i: number) =>
  ({ id: `r${i}`, readAt: "2026-07-11T09:00:00Z", readingMinutes: a.readingMinutes, completed: true,
     article: { ...a, emotion: a.emotion ?? NEUTRAL } }) as never;
const make = (arts: Art[]) => arts.map(entry);

test("empty history degrades to zeros, not NaN", () => {
  const s = summarizeHistory([]);
  assert.equal(s.count, 0);
  assert.equal(s.topicCount, 0);
  assert.equal(s.publisherCount, 0);
  assert.equal(s.avgReadingMinutes, 0);
  assert.equal(s.reportingShare, 0);
  assert.equal(s.topPublisherShare, 0);
  assert.equal(s.mostReadTopic, null);
  assert.equal(s.mostReadPublisher, null);
  assert.deepEqual(s.leanShare, { left: 0, center: 0, right: 0 });
  assert.deepEqual(s.emotion, []);
  assert.equal(s.politicalTilt, "balanced");
});

test("emotion distribution buckets each read by its dominant emotion", () => {
  const s = summarizeHistory(make([
    { topic: "T", publisher: "P", lean: 0, register: "reporting", readingMinutes: 3, emotion: { fear: 0.8, outrage: 0.1, analysis: 0.1, positive: 0, neutral: 0 } },
    { topic: "T", publisher: "P", lean: 0, register: "reporting", readingMinutes: 3, emotion: { fear: 0.1, outrage: 0.7, analysis: 0.2, positive: 0, neutral: 0 } },
    { topic: "T", publisher: "P", lean: 0, register: "reporting", readingMinutes: 3, emotion: { fear: 0, outrage: 0, analysis: 0.9, positive: 0.1, neutral: 0 } },
    { topic: "T", publisher: "P", lean: 0, register: "reporting", readingMinutes: 3 }, // neutral default
  ]));
  const m = Object.fromEntries(s.emotion.map((e) => [e.key, e.n]));
  assert.deepEqual(m, { fear: 1, outrage: 1, analysis: 1, neutral: 1 });
});

test("counts, distinct tallies, averages and concentration", () => {
  const s = summarizeHistory(make([
    { topic: "Politics", publisher: "Fox News", lean: 1.6, register: "opinion", readingMinutes: 4 },
    { topic: "Politics", publisher: "Fox News", lean: 1.2, register: "reporting", readingMinutes: 6 },
    { topic: "Business", publisher: "CNN", lean: -0.2, register: "reporting", readingMinutes: 2 },
  ]));
  assert.equal(s.count, 3);
  assert.equal(s.topicCount, 2);
  assert.equal(s.publisherCount, 2);
  assert.equal(s.mostReadTopic, "Politics");
  assert.equal(s.mostReadPublisher, "Fox News");
  assert.equal(s.topTopics[0].n, 2);
  assert.ok(Math.abs(s.topPublisherShare - 2 / 3) < 1e-9); // 2 of 3 reads from Fox News
  assert.equal(s.avgReadingMinutes, 4); // (4+6+2)/3
  assert.ok(Math.abs(s.reportingShare - 2 / 3) < 1e-9);
});

test("lean buckets use the tau=0.5 boundary (matches backend)", () => {
  const s = summarizeHistory(make([
    { topic: "T", publisher: "P", lean: 1.6, register: "reporting", readingMinutes: 3 },  // right
    { topic: "T", publisher: "Q", lean: -1.4, register: "reporting", readingMinutes: 3 }, // left
    { topic: "T", publisher: "R", lean: 0.1, register: "reporting", readingMinutes: 3 },  // center
    { topic: "T", publisher: "S", lean: 0.5, register: "reporting", readingMinutes: 3 },  // center (== tau)
  ]));
  assert.deepEqual(s.leanCounts, { left: 1, center: 2, right: 1 });
});

test("classifiers: tilt, breadth, reporting", () => {
  assert.equal(classifyTilt({ left: 0.7, right: 0.2 }), "left");
  assert.equal(classifyTilt({ left: 0.2, right: 0.7 }), "right");
  assert.equal(classifyTilt({ left: 0.5, right: 0.4 }), "balanced"); // <20pt gap
  assert.equal(classifyTilt({ left: 0.34, right: 0.33 }), "balanced"); // centre-heavy

  assert.equal(classifyBreadth(2, 2), "moderate"); // too few reads to judge
  assert.equal(classifyBreadth(10, 2), "narrow");
  assert.equal(classifyBreadth(10, 6), "broad");
  assert.equal(classifyBreadth(10, 3), "moderate");

  assert.equal(classifyReporting(0.7, 0.3), "reporting");
  assert.equal(classifyReporting(0.3, 0.5), "opinion");
  assert.equal(classifyReporting(0.5, 0.3), "mixed");
});

test("dayKey is a stable YYYY-MM-DD local key (same instant → same key)", () => {
  const k = dayKey("2026-07-11T09:00:00Z");
  assert.match(k, /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(dayKey("2026-07-11T09:00:00Z"), dayKey("2026-07-11T09:00:00.000Z"));
});

test("tally is deterministic (count desc, then name asc)", () => {
  assert.deepEqual(tally(["b", "a", "b", "a", "c"]), [
    { name: "a", n: 2 },
    { name: "b", n: 2 },
    { name: "c", n: 1 },
  ]);
});

const at = (readAt: string) =>
  ({ id: readAt, readAt, readingMinutes: 3, completed: true,
     article: { topic: "T", publisher: "P", lean: 0, register: "reporting", readingMinutes: 3, emotion: NEUTRAL } }) as never;

test("sessionize splits reads by gaps, newest session (and read) first", () => {
  const s = sessionize([
    at("2026-07-11T09:00:00Z"),
    at("2026-07-11T09:20:00Z"), // +20m → same session
    at("2026-07-11T11:00:00Z"), // +100m → new session
    at("2026-07-11T11:10:00Z"), // +10m → same
  ]);
  assert.equal(s.length, 2);
  assert.equal(s[0]!.reads.length, 2);
  assert.equal(s[0]!.start, "2026-07-11T11:00:00Z");
  assert.equal(s[0]!.end, "2026-07-11T11:10:00Z");
  assert.equal(s[1]!.start, "2026-07-11T09:00:00Z");
  assert.equal(s[1]!.end, "2026-07-11T09:20:00Z");
});

test("timeBucket boundaries", () => {
  assert.equal(timeBucket(5), "morning");
  assert.equal(timeBucket(11), "morning");
  assert.equal(timeBucket(12), "afternoon");
  assert.equal(timeBucket(16), "afternoon");
  assert.equal(timeBucket(17), "evening");
  assert.equal(timeBucket(21), "evening");
  assert.equal(timeBucket(22), "night");
  assert.equal(timeBucket(4), "night");
});

test("readingPattern: this-week volume, session count, avg size (injectable now)", () => {
  const now = new Date("2026-07-11T12:00:00Z").getTime();
  const p = readingPattern([
    at("2026-07-11T09:00:00Z"), // today, session A
    at("2026-07-11T09:10:00Z"), // today, session A
    at("2026-07-11T15:00:00Z"), // today, session B (>45m gap)
    at("2026-07-01T09:00:00Z"), // 10 days ago → own day/session, outside the week
  ], now);
  assert.equal(p.total, 4);
  assert.equal(p.articlesThisWeek, 3);
  assert.equal(p.sessionCount, 3); // Jul 11: 2 sessions, Jul 1: 1
  assert.ok(Math.abs(p.avgSessionSize - 4 / 3) < 1e-9);
  assert.notEqual(p.preferredTime, null);
});
