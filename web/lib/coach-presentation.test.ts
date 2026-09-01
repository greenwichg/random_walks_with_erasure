/**
 * M5 — coach presentation selection (node --test, type-stripped like @ih/core's i18n tests).
 *
 * Proves the progressive-enhancement contract of the coach page: every helper returns its
 * v1-neutral value for a v1 payload (no echo, no chips, metric-key labels unchanged), and
 * selects — never invents — the v2 affordances when the optional fields are present. Also
 * pins the licensing guarantee for citation labels: every `metric.<key>.label` this module
 * can emit exists in en.json, and unknown engine keys map to null (the caller shows the raw
 * key), never a missing-catalog lookup.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  citationLabelKey,
  lastEcho,
  activeFollowUps,
  trendLabelKey,
  weeklyTrendDelta,
  weeklyInsights,
  citationsBeyondCard,
} from "@ih/core/logic/coach-presentation";
import type { CoachMessage } from "@ih/core/domain/types";

const HERE = dirname(fileURLToPath(import.meta.url));
const EN = JSON.parse(
  readFileSync(join(HERE, "..", "..", "packages", "core", "i18n", "messages", "en.json"), "utf8"),
) as Record<string, string>;

const msg = (over: Partial<CoachMessage>): CoachMessage => ({
  id: "m1",
  role: "assistant",
  content: "c",
  createdAt: "2026-07-12T00:00:00Z",
  ...over,
});

// ---------------------------------------------------------------- citation labels
test("every metric-key citation label exists in en.json (licensing)", () => {
  for (const key of [
    "topicDiversity", "sourceDiversity", "reportingRatio", "emotionalBalance",
    "echoChamber", "viewpointBalance", "openMindedness", "confidence",
  ]) {
    const label = citationLabelKey(key);
    assert.equal(label, `metric.${key}.label`);
    assert.ok(EN[label!], `${label} must exist in en.json`);
  }
});

test("engine evidence keys are NOT catalog lookups — null tells the caller to show the raw key", () => {
  for (const key of ["served", "matched", "sourceShare.NPR", "verdict", "snapshots", ""]) {
    assert.equal(citationLabelKey(key), null);
  }
});

// ---------------------------------------------------------------- echo round-trip selection
test("v1 transcript (no echo anywhere) yields undefined — the request body stays v1", () => {
  assert.equal(lastEcho([msg({}), msg({ role: "user" }), msg({})]), undefined);
});

test("the MOST RECENT assistant echo wins; user turns and echo-less replies are skipped", () => {
  const older = { v: 1, turns: [{ role: "coach", intent: "EXPLAIN.metric" }] };
  const newer = { v: 1, turns: [{ role: "coach", intent: "ACT.suggest" }] };
  const transcript = [
    msg({ echo: older }),
    msg({ echo: newer }),
    msg({ role: "user" }),          // mid-thought user turn carries nothing
    msg({}),                        // a v1/error reply must not erase the carried state
  ];
  assert.equal(lastEcho(transcript), newer);
});

// ---------------------------------------------------------------- follow-up chips
test("v1 replies produce no chips (static starters keep their exact behaviour)", () => {
  assert.equal(activeFollowUps([msg({})]), null);
  assert.equal(activeFollowUps([msg({ followUps: [] })]), null);
  assert.equal(activeFollowUps([]), null);
});

test("chips come only from the LAST message, and only when it is an assistant reply", () => {
  const withChips = msg({ followUps: ["Why is it low?", "Suggest something"] });
  assert.deepEqual(activeFollowUps([msg({}), withChips]), ["Why is it low?", "Suggest something"]);
  // after the user sends, the offer is stale — nothing to accept mid-flight
  assert.equal(activeFollowUps([withChips, msg({ role: "user", followUps: ["x"] })]), null);
});

// --------------------------------------------------------------------------- //
// Weekly Review presentation
// --------------------------------------------------------------------------- //
test("trendLabelKey: analytics catalog keys for known series, null for unknown", () => {
  assert.equal(trendLabelKey("politicalDiversity"), "analytics.politicalDiversity");
  assert.equal(trendLabelKey("healthImprovement"), "analytics.healthImprovement");
  assert.equal(trendLabelKey("mystery"), null);
});

test("weeklyTrendDelta: signed delta, null when either end is unmeasured", () => {
  assert.equal(weeklyTrendDelta({ first: 70, last: 69 }), -1);
  assert.equal(weeklyTrendDelta({ first: 84, last: 84 }), 0);
  assert.equal(weeklyTrendDelta({ first: null, last: 80 }), null);
});

test("weeklyInsights: biggest mover wins, slip preferred on ties, capped at two", () => {
  const out = weeklyInsights({
    reads: 80,
    topPublishers: [{ name: "New York Times", reads: 15 }],
    trends: [
      { metric: "healthImprovement", first: 70, last: 69 },
      { metric: "politicalDiversity", first: 84, last: 81 },
      { metric: "publisherDiversity", first: 100, last: 100 },
    ],
  });
  assert.deepEqual(out, [{ kind: "slip", metric: "politicalDiversity", delta: -3 }]);
});

test("weeklyInsights: all-flat reports steady; heavy concentration flagged at >=40%", () => {
  const out = weeklyInsights({
    reads: 30,
    topPublishers: [{ name: "New York Post", reads: 14 }],
    trends: [{ metric: "publisherDiversity", first: 100, last: 100 }],
  });
  assert.deepEqual(out, [
    { kind: "steady" },
    { kind: "concentration", publisher: "New York Post", share: 47 },
  ]);
});

test("weeklyInsights: empty payload derives nothing (honest omission)", () => {
  assert.deepEqual(weeklyInsights({ reads: null, topPublishers: [], trends: [] }), []);
  assert.deepEqual(
    weeklyInsights({
      reads: 10,
      topPublishers: [{ name: "NPR", reads: 2 }],   // 20% — below the concentration bar
      trends: [{ metric: "healthImprovement", first: null, last: null }],
    }),
    [],
  );
});

// --------------------------------------------------------------------------- //
// citationsBeyondCard — the weekly reply must not say everything twice.
// --------------------------------------------------------------------------- //
// Reported from production 2026-09-01: the Weekly Review card rendered "5 Reads / 4 Outlets /
// 20 min" and its trend tiles, and directly beneath it the same numbers again as raw chips
// (totalReads: 5, readingGoalMinutes: 20, healthImprovement.first: 67, …). Coverage is derived
// from the payload, so these tests pin BOTH directions: a fact the card shows loses its chip, a
// fact it cannot show keeps one.

const REVIEW = {
  reads: 5,
  outlets: 4,
  goalMinutes: 20,
  storedGoals: null,
  topPublishers: [{ name: "decider.com" }],
  trends: [{ metric: "healthImprovement" }, { metric: "politicalDiversity" }],
};

test("a citation the Weekly Review card renders is dropped", () => {
  const cites = [
    { metric: "totalReads", value: 5 },
    { metric: "distinctOutlets", value: 4 },
    { metric: "readingGoalMinutes", value: 20 },
    { metric: "topOutlets", value: "decider.com" },
    { metric: "healthImprovement.first", value: 67 },
    { metric: "healthImprovement.last", value: 68 },
    { metric: "politicalDiversity.first", value: 64 },
  ];
  assert.deepEqual(citationsBeyondCard(cites, REVIEW), [],
    "every chip in the screenshot is a fact the card already shows");
});

test("a citation the card does NOT render survives", () => {
  const cites = [
    { metric: "totalReads", value: 5 },
    { metric: "sourceShare.NPR", value: "12%" },          // no card section renders this
    { metric: "topicDiversity.last", value: 71 },          // a trend absent from this payload
  ];
  assert.deepEqual(citationsBeyondCard(cites, REVIEW).map((c) => c.metric),
    ["sourceShare.NPR", "topicDiversity.last"],
    "coverage is what the card shows — never a blanket suppression");
});

test("an unmeasured fact keeps its chip: coverage tracks the payload, not a key list", () => {
  const thin = { ...REVIEW, reads: null, goalMinutes: null, topPublishers: [] };
  const cites = [{ metric: "totalReads", value: 5 }, { metric: "readingGoalMinutes", value: 20 },
                 { metric: "topOutlets", value: "x" }, { metric: "distinctOutlets", value: 4 }];
  assert.deepEqual(citationsBeyondCard(cites, thin).map((c) => c.metric),
    ["totalReads", "readingGoalMinutes", "topOutlets"],
    "the card omits null rows, so those chips are the only place the fact appears");
});

test("every non-weekly reply passes its citations through untouched", () => {
  const cites = [{ metric: "totalReads", value: 5 }, { metric: "echoChamber", value: 42 }];
  assert.deepEqual(citationsBeyondCard(cites, null), cites, "v1/other intents are unchanged");
  assert.deepEqual(citationsBeyondCard(cites, undefined), cites);
  assert.deepEqual(citationsBeyondCard(undefined, REVIEW), [], "no citations, no chips");
});
