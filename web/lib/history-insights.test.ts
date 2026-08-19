/**
 * Phase 1 — history-insights tests (node --test, type-stripped like the other lib tests).
 * Proves the aggregation (counts, shares, top-N, concentration, averages) and the softly-thresholded
 * classifiers used by the Reflection/Insights copy, including the empty-set degradation.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

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
  preferredTimeBucket,
  localHour,
  parseReadAt,
  withExplicitZone,
} from "./history-insights.ts";

type Art = { topic: string; publisher: string; lean: number | null; register: string; readingMinutes: number;
             emotion?: Record<string, number> };
const entry = (a: Art, i: number) =>
  ({ id: `r${i}`, readAt: "2026-07-11T09:00:00Z", readingMinutes: a.readingMinutes, completed: true,
     // No emotion on the fixture = no emotion on the wire (null, never a neutral default — L2.2).
     article: { ...a, emotion: a.emotion ?? null } }) as never;
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
    { topic: "T", publisher: "P", lean: 0, register: "reporting", readingMinutes: 3 }, // no emotion signal — counted nowhere, never as neutral
  ]));
  const m = Object.fromEntries(s.emotion.map((e) => [e.key, e.n]));
  assert.deepEqual(m, { fear: 1, outrage: 1, analysis: 1 });
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

test("unknown (null) lean is excluded from counts and shares (L2.2), not bucketed as center", () => {
  // Two known-left reads + two unknown-outlet reads (lean null). The unknowns must NOT become
  // "center": counts drop them and shares use the KNOWN-lean denominator (2), so a reader whose
  // known reads are all left reads as fully left — the unknowns neither dilute nor tilt the mix.
  const s = summarizeHistory(make([
    { topic: "T", publisher: "P", lean: -1.4, register: "reporting", readingMinutes: 3 }, // left
    { topic: "T", publisher: "Q", lean: -0.8, register: "reporting", readingMinutes: 3 }, // left
    { topic: "T", publisher: "R", lean: null, register: "reporting", readingMinutes: 3 }, // unknown
    { topic: "T", publisher: "S", lean: null, register: "reporting", readingMinutes: 3 }, // unknown
  ]));
  assert.deepEqual(s.leanCounts, { left: 2, center: 0, right: 0 }); // unknowns excluded, not center
  assert.deepEqual(s.leanShare, { left: 1, center: 0, right: 0 });  // denominator = 2 known, not 4
  assert.equal(s.politicalTilt, "left");                            // Reflection/Daily-Summary copy
});

test("all-unknown lean history degrades to zero shares (no divide-by-zero), balanced tilt", () => {
  // The day-scoped Daily Summary + Reflection copy read `leanShare`; with every read from an unknown
  // outlet it must be all-zero (not NaN) and the tilt balanced, while non-lean insights (volume,
  // reporting mix) are untouched — an unknown lean silences only the political signal.
  const s = summarizeHistory(make([
    { topic: "T", publisher: "P", lean: null, register: "reporting", readingMinutes: 3 },
    { topic: "T", publisher: "Q", lean: null, register: "opinion", readingMinutes: 3 },
  ]));
  assert.deepEqual(s.leanCounts, { left: 0, center: 0, right: 0 });
  assert.deepEqual(s.leanShare, { left: 0, center: 0, right: 0 });
  assert.equal(s.politicalTilt, "balanced");
  assert.equal(s.count, 2);                              // reads still count for volume/other insights
  assert.ok(Math.abs(s.reportingShare - 0.5) < 1e-9);   // non-lean insights unaffected by unknown lean
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
     article: { topic: "T", publisher: "P", lean: 0, register: "reporting", readingMinutes: 3, emotion: null } }) as never;

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
  assert.equal(p.preferredTime, null);   // 4 reads is below the habit floor — "—", not a claim
});

// --------------------------------------------------------------------------- //
// Preferred time: reader-local bucketing + rolling window (the "current habits" contract).
// --------------------------------------------------------------------------- //
const NOW = Date.parse("2026-07-27T12:00:00Z");
const daysAgo = (n: number, hhmmZ: string) => {
  const d = new Date(NOW - n * 86400000);
  return `${d.toISOString().slice(0, 10)}T${hhmmZ}`;
};
const many = (n: number, iso: string) => Array.from({ length: n }, () => at(iso));

test("localHour reads the reader's wall clock, not UTC", () => {
  // One instant, three readers: 02:00Z is late evening in New York, breakfast in Delhi.
  assert.equal(localHour("2026-07-15T02:00:00Z", "UTC"), 2);
  assert.equal(localHour("2026-07-15T02:00:00Z", "America/New_York"), 22);
  assert.equal(localHour("2026-07-15T02:00:00Z", "Asia/Kolkata"), 7);
  assert.equal(localHour("2026-07-15T04:00:00Z", "America/New_York"), 0); // midnight, not "24"
});

test("the SAME instant buckets differently for readers in different zones", () => {
  // 12:00Z: mid-afternoon in London-less UTC terms, breakfast in New York, evening in Delhi.
  const reads = many(6, "2026-07-15T12:00:00Z");
  assert.equal(preferredTimeBucket(reads, { now: NOW, timeZone: "America/New_York" }), "morning");
  assert.equal(preferredTimeBucket(reads, { now: NOW, timeZone: "UTC" }), "afternoon");
  assert.equal(preferredTimeBucket(reads, { now: NOW, timeZone: "Asia/Kolkata" }), "evening");
  // A half-hour offset zone must not round into the wrong bucket: 11:45Z is 17:15 in Kolkata.
  assert.equal(
    preferredTimeBucket(many(6, "2026-07-15T11:45:00Z"), { now: NOW, timeZone: "Asia/Kolkata" }),
    "evening",
  );
});

test("bucket transitions land on the documented edges, in local time", () => {
  const zone = "America/New_York"; // UTC-4 in July
  const edge = (utcHour: string) =>
    preferredTimeBucket(many(6, `2026-07-15T${utcHour}:00:00Z`), { now: NOW, timeZone: zone });
  assert.equal(edge("08"), "night");      // 04:00 local → still night
  assert.equal(edge("09"), "morning");    // 05:00 local → morning starts
  assert.equal(edge("15"), "morning");    // 11:00 local → last morning hour
  assert.equal(edge("16"), "afternoon");  // 12:00 local
  assert.equal(edge("20"), "afternoon");  // 16:00 local → last afternoon hour
  assert.equal(edge("21"), "evening");    // 17:00 local
  assert.equal(edge("01"), "evening");    // 21:00 local (prev day) → last evening hour
  assert.equal(edge("02"), "night");      // 22:00 local (prev day)
});

test("rolling window: an old habit stops outvoting the current one", () => {
  // 40 morning reads from months ago, 8 evening reads in the last fortnight.
  const stale = Array.from({ length: 40 }, (_, i) => at(daysAgo(90 + i, "13:00:00Z")));
  const recent = Array.from({ length: 8 }, (_, i) => at(daysAgo(i + 1, "23:00:00Z")));
  const entries = [...stale, ...recent];
  assert.equal(preferredTimeBucket(entries, { now: NOW, timeZone: "UTC" }), "night");   // 23:00Z
  // Lifetime (windowDays: 0) is what the card used to show — the stale majority wins.
  assert.equal(
    preferredTimeBucket(entries, { now: NOW, timeZone: "UTC", windowDays: 0 }),
    "afternoon",
  );
});

test("no habit is claimed from too few reads, or from an empty window", () => {
  const few = Array.from({ length: 4 }, (_, i) => at(daysAgo(i + 1, "09:00:00Z")));
  assert.equal(preferredTimeBucket(few, { now: NOW, timeZone: "UTC" }), null);       // 4 < floor
  assert.equal(preferredTimeBucket([...few, at(daysAgo(2, "09:00:00Z"))],
                                   { now: NOW, timeZone: "UTC" }), "morning");        // 5 → claimed
  const ancient = Array.from({ length: 50 }, (_, i) => at(daysAgo(60 + i, "09:00:00Z")));
  assert.equal(preferredTimeBucket(ancient, { now: NOW, timeZone: "UTC" }), null);   // window empty
  assert.equal(preferredTimeBucket([], { now: NOW }), null);
});

test("ties resolve deterministically, never by input order", () => {
  const morning = many(3, daysAgo(1, "09:00:00Z"));
  const evening = many(3, daysAgo(1, "19:00:00Z"));
  const a = preferredTimeBucket([...morning, ...evening], { now: NOW, timeZone: "UTC" });
  const b = preferredTimeBucket([...evening, ...morning], { now: NOW, timeZone: "UTC" });
  assert.equal(a, b);
  assert.equal(a, "morning");   // fixed precedence: morning < afternoon < evening < night
});

test("readingPattern exposes the windowed preferred time", () => {
  const entries = [
    ...Array.from({ length: 30 }, (_, i) => at(daysAgo(120 + i, "09:00:00Z"))),  // old mornings
    ...Array.from({ length: 6 }, (_, i) => at(daysAgo(i + 1, "19:00:00Z"))),     // recent evenings
  ];
  assert.equal(readingPattern(entries, NOW).preferredTime, "evening");
  assert.equal(readingPattern(entries, NOW).total, 36);   // volume facts stay over the whole set
});

// --------------------------------------------------------------------------- //
// The shape the engine ACTUALLY serves.
//
// Every fixture above carries a `Z`. Production mostly does not: only the browser extension sends
// an `observedAt`, so an in-app read falls back to the row's `created_at`, which SQLite hands back
// naive — `2026-08-19T15:01:14.807509`, no offset. ECMAScript reads that as LOCAL, so the card was
// bucketing by the SERVER's clock: one 15:01 UTC read read as "afternoon" for everyone, when it was
// evening in Delhi and morning in New York.
//
// These assertions are deliberately about the STRING, not about Date maths. On a UTC machine
// `new Date(bare)` and `new Date(bare + "Z")` agree, so a clock-based test passes on a UTC CI
// whether or not the bug is present — which is precisely how it shipped.
// --------------------------------------------------------------------------- //

test("a bare engine timestamp is marked UTC, because that is what it is", () => {
  assert.equal(withExplicitZone("2026-08-19T15:01:14.807509"), "2026-08-19T15:01:14.807509Z");
  assert.equal(withExplicitZone("2026-08-19T15:01:14"), "2026-08-19T15:01:14Z");
  assert.equal(withExplicitZone("2026-08-19 15:01:14"), "2026-08-19T15:01:14Z"); // space separator
});

test("a timestamp that already states its offset is left alone", () => {
  for (const iso of [
    "2026-08-19T15:01:14.807Z",
    "2026-08-19T15:01:14+05:30",
    "2026-08-19T15:01:14-05:00",
    "2026-08-19T15:01:14-0500",
    "2026-08-19T15:01:14+00:00",
  ]) {
    assert.equal(withExplicitZone(iso), iso, `${iso} must not be rewritten`);
  }
});

test("a date-only key is not a date-time and is not touched", () => {
  // ECMAScript already reads a bare YYYY-MM-DD as UTC; appending Z here would be a no-op at best
  // and a parse error at worst.
  assert.equal(withExplicitZone("2026-08-19"), "2026-08-19");
  assert.equal(withExplicitZone(""), "");
});

test("the bare and marked forms of one instant parse identically", () => {
  const bare = "2026-08-19T15:01:14.807509";
  assert.equal(parseReadAt(bare).getTime(), Date.parse(bare.slice(0, 23) + "Z"));
  assert.equal(parseReadAt(bare).toISOString(), "2026-08-19T15:01:14.807Z");
});

test("the served shape buckets by the READER's clock, not the server's", () => {
  // The regression, end to end: six in-app reads at 15:01 UTC, exactly as the engine serves them.
  const served = Array.from({ length: 6 }, () => at("2026-07-15T15:01:14.807509"));
  assert.equal(preferredTimeBucket(served, { now: NOW, timeZone: "Asia/Kolkata" }), "evening");   // 20:31
  assert.equal(preferredTimeBucket(served, { now: NOW, timeZone: "America/New_York" }), "morning"); // 11:01
  assert.equal(preferredTimeBucket(served, { now: NOW, timeZone: "UTC" }), "afternoon");
  // Before the fix all three said "afternoon" — the server's bucket, wearing the reader's name.
});

test("the rolling window and the week count read the served shape too", () => {
  // `at < cutoff` and `>= weekAgo` parse the same strings; a misparse shifts a read by the offset
  // and can push it out of a window it belongs in.
  const bare = (n: number, hms: string) =>
    `${new Date(NOW - n * 86400000).toISOString().slice(0, 10)}T${hms}`;
  const entries = Array.from({ length: 6 }, (_, i) => at(bare(i + 1, "23:30:00.123456")));
  assert.equal(preferredTimeBucket(entries, { now: NOW, timeZone: "UTC" }), "night");
  assert.equal(readingPattern(entries, NOW).articlesThisWeek, 6);
});

test("dayKey agrees with the bucket about which instant a bare stamp names", () => {
  // Calendar and Timeline group by dayKey; the preferred-time card buckets by localHour. Both go
  // through the same parse, so they can never disagree about the same read.
  const bare = "2026-08-19T15:01:14.807509";
  assert.equal(dayKey(bare), dayKey("2026-08-19T15:01:14.807509Z"));
});

test("the DEVICE-zone path — the one production uses — buckets in the reader's zone", () => {
  // `readingPattern` calls `preferredTimeBucket` with no `timeZone`, so the real code path is
  // `Date#getHours()` against the browser's own zone. Every other test here passes an explicit
  // IANA zone, which exercises the Intl path instead and leaves the production one uncovered.
  // V8 caches the zone, so it cannot be switched inside this process — a child that starts with
  // TZ already set is the only honest way to stand where the reader stands.
  const mod = fileURLToPath(new URL("./history-insights.ts", import.meta.url));
  const script = `
    import { preferredTimeBucket } from ${JSON.stringify(mod)};
    const served = Array.from({ length: 6 }, () => ({ readAt: "2026-07-15T15:01:14.807509" }));
    process.stdout.write(String(preferredTimeBucket(served, { now: Date.parse("2026-07-27T12:00:00Z") })));
  `;
  for (const [tz, expected] of [
    ["Asia/Kolkata", "evening"],       // 20:31 local
    ["America/New_York", "morning"],   // 11:01 local
    ["Europe/Berlin", "evening"],      // 17:01 local
    ["UTC", "afternoon"],
  ] as const) {
    const r = spawnSync(process.execPath, ["--input-type=module", "-e", script], {
      env: { ...process.env, TZ: tz },
      encoding: "utf-8",
    });
    assert.equal(r.status, 0, r.stderr);
    assert.equal(r.stdout, expected, `a reader in ${tz} must be told ${expected}`);
  }
});
