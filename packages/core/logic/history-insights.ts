/**
 * Reading-History insights — a pure, testable aggregation over a set of reads (Phase 1 of the
 * History redesign). It powers the Information Health strip and the Reflection/Insights section:
 * descriptive counts and shares of the reads *currently in view*, deliberately distinct from the
 * engine's scored, all-time Information Health metrics (Dashboard / Report). No React, no i18n —
 * callers map the returned discriminators to catalog strings.
 */
import type { HistoryEntry, EmotionShare } from "../domain/types.ts";

/**
 * Lean → bucket at tau=0.5. Inlined (mirrors lib/political.ts::leanBucket and the backend) so this
 * module has NO runtime imports and stays runnable under `node --test`, which can't resolve the
 * `@/` alias graph. Keep the boundary in sync with lib/political.ts.
 */
const LEAN_TAU = 0.5;
function bucketOf(lean: number): "left" | "center" | "right" {
  if (lean < -LEAN_TAU) return "left";
  if (lean > LEAN_TAU) return "right";
  return "center";
}

/** The dominant emotion of a share vector (inlined mirror of lib/political.ts::dominantEmotion). */
function dominantOf(e: EmotionShare): keyof EmotionShare {
  const keys = Object.keys(e) as (keyof EmotionShare)[];
  return keys.reduce((a, b) => (e[a] >= e[b] ? a : b));
}

export type PoliticalTilt = "left" | "right" | "balanced";
export type TopicBreadth = "narrow" | "moderate" | "broad";
export type ReportingTilt = "reporting" | "opinion" | "mixed";
export type Concentration = "concentrated" | "spread";

export interface Tallied {
  name: string;
  n: number;
}

export interface HistoryInsights {
  count: number;
  topicCount: number;
  publisherCount: number;
  leanCounts: { left: number; center: number; right: number };
  /**
   * Fractions (0..1) of the KNOWN-lean reads in each bucket — the denominator is the number of reads
   * whose outlet lean is known, so unknown-lean reads (null) are excluded, never counted as centre
   * (L2.2). All 0 when there are no known-lean reads (incl. the empty history).
   */
  leanShare: { left: number; center: number; right: number };
  reportingShare: number; // 0..1
  opinionShare: number; // 0..1
  avgReadingMinutes: number;
  /** Reads bucketed by their dominant emotion, present emotions only, most-frequent first. */
  emotion: { key: keyof EmotionShare; n: number }[];
  topTopics: Tallied[];
  topPublishers: Tallied[];
  mostReadTopic: string | null;
  mostReadPublisher: string | null;
  /** Share (0..1) of reads from the single most-read publisher — the concentration signal. */
  topPublisherShare: number;
  // ---- derived, softly-thresholded classifiers (used for the Reflection/Insights copy) ----
  politicalTilt: PoliticalTilt;
  topicBreadth: TopicBreadth;
  reportingTilt: ReportingTilt;
  concentration: Concentration;
}

/** An ISO string that states its offset: `…Z`, `…+05:30`, `…-0500`. */
const ZONED = /(?:[Zz]|[+-]\d{2}:?\d{2})$/;
/** A date-TIME (as opposed to a bare `YYYY-MM-DD`, which ECMAScript already reads as UTC). */
const DATE_TIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;

/**
 * Parse a stored read timestamp. **Every `readAt` in this module goes through here.**
 *
 * The engine serves two shapes. A client-supplied `observedAt` carries its offset (the extension
 * sends `toISOString()`). But the web app never sends one, so those reads fall back to the row's
 * own `created_at` — which IS UTC (`store._utcnow()`), except that SQLite's plain `DateTime`
 * column drops the tzinfo on the round trip and `.isoformat()` then writes no marker at all:
 * `2026-08-19T15:01:14.807509`.
 *
 * ECMAScript reads a bare date-time as LOCAL. So the browser was shifting every in-app read by
 * the reader's own UTC offset, and the Preferred-time card reported the SERVER's clock: a 15:01
 * UTC read showed "afternoon" to a Delhi reader whose evening it was, and to a New York reader
 * whose morning it was. Silent, because "Afternoons" is a plausible-looking answer.
 *
 * A bare stamp is therefore read as UTC, which is what it is.
 */
export function parseReadAt(iso: string): Date {
  return new Date(withExplicitZone(iso));
}

/**
 * The string half of `parseReadAt`, split out so the guarantee is testable WITHOUT a clock.
 *
 * This bug is invisible on a UTC machine — `new Date(bare)` and `new Date(bare + "Z")` agree
 * there — which is how it got past a UTC CI in the first place. A test that asserts Date maths
 * can only catch it on a developer who happens to sit in another zone. A test that asserts this
 * string catches it everywhere.
 */
export function withExplicitZone(iso: string): string {
  if (typeof iso === "string" && DATE_TIME.test(iso) && !ZONED.test(iso)) {
    return `${iso.replace(" ", "T")}Z`;
  }
  return iso;
}

/**
 * Stable local-day key (YYYY-MM-DD) for a timestamp — the shared identifier that syncs the Calendar
 * selection to the Timeline grouping (both bucket reads by local day). Pure; no imports.
 */
export function dayKey(iso: string): string {
  const d = parseReadAt(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Count occurrences, returned most-frequent first (ties broken alphabetically for determinism). */
export function tally(names: string[]): Tallied[] {
  const m = new Map<string, number>();
  for (const x of names) if (x) m.set(x, (m.get(x) ?? 0) + 1);
  return [...m.entries()]
    .map(([name, n]) => ({ name, n }))
    .sort((a, b) => b.n - a.n || a.name.localeCompare(b.name));
}

/** ≥50 % one side AND ≥20 pts clear of the other → that side; otherwise balanced (incl. centre-heavy). */
export function classifyTilt(s: { left: number; right: number }): PoliticalTilt {
  if (s.left >= 0.5 && s.left - s.right >= 0.2) return "left";
  if (s.right >= 0.5 && s.right - s.left >= 0.2) return "right";
  return "balanced";
}

/** Too few reads to judge → moderate; else ≤2 topics = narrow, ≥5 = broad. */
export function classifyBreadth(count: number, topics: number): TopicBreadth {
  if (count < 3) return "moderate";
  if (topics <= 2) return "narrow";
  if (topics >= 5) return "broad";
  return "moderate";
}

/** ≥60 % reporting = reporting-heavy; else ≥40 % opinion = opinion-heavy; else mixed. */
export function classifyReporting(reportingShare: number, opinionShare: number): ReportingTilt {
  if (reportingShare >= 0.6) return "reporting";
  if (opinionShare >= 0.4) return "opinion";
  return "mixed";
}

export function summarizeHistory(entries: HistoryEntry[]): HistoryInsights {
  const arts = entries.map((e) => e.article);
  const count = arts.length;
  const share = (n: number) => (count ? n / count : 0);

  const topics = tally(arts.map((a) => a.topic));
  const publishers = tally(arts.map((a) => a.publisher));

  // Lean distribution over KNOWN-lean reads only: an unknown lean (an outlet the registry doesn't
  // know) is excluded, never bucketed as "center" (L2.2). Shares sum to 1 over the known-lean reads,
  // so a reader with all-known reads is unchanged; the denominator drops only the unknowns.
  const leanCounts = { left: 0, center: 0, right: 0 };
  let leanKnown = 0;
  for (const a of arts) {
    if (a.lean == null) continue;
    leanKnown++;
    leanCounts[bucketOf(a.lean)]++;
  }
  const leanShare = {
    left: leanKnown ? leanCounts.left / leanKnown : 0,
    center: leanKnown ? leanCounts.center / leanKnown : 0,
    right: leanKnown ? leanCounts.right / leanKnown : 0,
  };

  const reportingShare = share(arts.filter((a) => a.register === "reporting").length);
  const opinionShare = share(arts.filter((a) => a.register === "opinion").length);
  const avgReadingMinutes = count
    ? arts.reduce((s, a) => s + (a.readingMinutes || 0), 0) / count
    : 0;

  const emo = new Map<keyof EmotionShare, number>();
  for (const a of arts) {
    if (!a.emotion) continue;   // no emotion signal: counted nowhere, never as neutral (L2.2)
    const k = dominantOf(a.emotion);
    emo.set(k, (emo.get(k) ?? 0) + 1);
  }
  const emotion = [...emo.entries()]
    .map(([key, n]) => ({ key, n }))
    .sort((a, b) => b.n - a.n || a.key.localeCompare(b.key));

  const topPublisher = publishers[0];
  const topPublisherShare = topPublisher && count ? topPublisher.n / count : 0;

  return {
    count,
    topicCount: topics.length,
    publisherCount: publishers.length,
    leanCounts,
    leanShare,
    reportingShare,
    opinionShare,
    avgReadingMinutes,
    emotion,
    topTopics: topics.slice(0, 3),
    topPublishers: publishers.slice(0, 3),
    mostReadTopic: topics[0]?.name ?? null,
    mostReadPublisher: publishers[0]?.name ?? null,
    topPublisherShare,
    politicalTilt: classifyTilt(leanShare),
    topicBreadth: classifyBreadth(count, topics.length),
    reportingTilt: classifyReporting(reportingShare, opinionShare),
    concentration: topPublisherShare >= 0.5 ? "concentrated" : "spread",
  };
}

// ---- reading sessions + behavioural pattern (Phase 3) ----

export interface ReadingSession {
  start: string; // ISO of the oldest read in the session
  end: string; // ISO of the newest read
  reads: HistoryEntry[]; // newest-first
}

/** A new session begins when the gap between consecutive reads exceeds this many minutes. */
export const SESSION_GAP_MINUTES = 45;

/**
 * Split reads (typically one day's) into contiguous reading sessions by read-time gaps. Accepts any
 * order; returns newest session first, reads newest-first within each. Pure.
 */
export function sessionize(entries: HistoryEntry[], gapMinutes = SESSION_GAP_MINUTES): ReadingSession[] {
  const sorted = [...entries].sort((a, b) => parseReadAt(b.readAt).getTime() - parseReadAt(a.readAt).getTime());
  const out: ReadingSession[] = [];
  let cur: HistoryEntry[] = [];
  const flush = () => {
    if (cur.length) out.push({ start: cur[cur.length - 1]!.readAt, end: cur[0]!.readAt, reads: cur });
  };
  for (const e of sorted) {
    if (cur.length === 0) {
      cur = [e];
      continue;
    }
    const gapMs = parseReadAt(cur[cur.length - 1]!.readAt).getTime() - parseReadAt(e.readAt).getTime();
    if (gapMs > gapMinutes * 60000) {
      flush();
      cur = [e];
    } else {
      cur.push(e);
    }
  }
  flush();
  return out;
}

export type TimeBucket = "morning" | "afternoon" | "evening" | "night";

/** Local hour → coarse time-of-day bucket (morning 5–11, afternoon 12–16, evening 17–21, night). */
export function timeBucket(hour: number): TimeBucket {
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 17) return "afternoon";
  if (hour >= 17 && hour < 22) return "evening";
  return "night";
}

/**
 * Hour-of-day (0–23) for an instant, in the reader's own wall clock.
 *
 * A read is bucketed by when it felt like morning TO THE READER, never by UTC: "07:30 in Delhi" is
 * a morning read even though the instant is 02:00 UTC the same day. With no `timeZone` this uses
 * the device's zone (`Date#getHours`) — the browser is the reader's clock, and this module only
 * ever runs client-side. Tests pass an explicit IANA zone so boundaries are provable without
 * mutating the process clock.
 */
export function localHour(iso: string, timeZone?: string): number {
  const d = parseReadAt(iso);
  if (!timeZone) return d.getHours();
  const hour = new Intl.DateTimeFormat("en-US", { hour: "numeric", hour12: false, timeZone }).format(d);
  return Number(hour) % 24; // some ICU builds render midnight as "24"
}

/** Fixed precedence so an exact tie is resolved deterministically (never by input order). */
const BUCKET_ORDER: TimeBucket[] = ["morning", "afternoon", "evening", "night"];

export interface PreferredTimeOptions {
  now?: number;
  /** Rolling window in days (default 30). 0 / negative ⇒ no window (lifetime). */
  windowDays?: number;
  /** Floor below which no habit is claimed (default 5) — a handful of reads is not a pattern. */
  minReads?: number;
  /** IANA zone; omitted ⇒ the device's own zone. */
  timeZone?: string;
}

/**
 * The reader's modal time of day over a ROLLING WINDOW (default the last 30 days).
 *
 * Deliberately not lifetime: the card answers "when do you read?" in the present tense, and a
 * lifetime mode is dominated by whatever the reader used to do — someone who read every morning for
 * a year and has read only evenings for the last month would keep being told "Mornings" essentially
 * forever, and the metric would get *more* stubborn as history grew. Returns `null` (the card shows
 * "—") when the window holds fewer than `minReads` reads: honest silence beats a confident label
 * derived from three data points.
 */
export function preferredTimeBucket(
  entries: HistoryEntry[],
  { now = Date.now(), windowDays = 30, minReads = 5, timeZone }: PreferredTimeOptions = {},
): TimeBucket | null {
  const cutoff = windowDays > 0 ? now - windowDays * 24 * 60 * 60 * 1000 : -Infinity;
  const counts = new Map<TimeBucket, number>();
  let inWindow = 0;
  for (const e of entries) {
    const at = parseReadAt(e.readAt).getTime();
    if (!Number.isFinite(at) || at < cutoff || at > now) continue; // future stamps are not habits
    inWindow += 1;
    const bkt = timeBucket(localHour(e.readAt, timeZone));
    counts.set(bkt, (counts.get(bkt) ?? 0) + 1);
  }
  if (inWindow < minReads) return null;

  let best: TimeBucket | null = null;
  let max = 0;
  for (const bkt of BUCKET_ORDER) {
    const n = counts.get(bkt) ?? 0;
    if (n > max) {
      max = n;
      best = bkt;
    }
  }
  return best;
}

export interface ReadingPattern {
  total: number;
  articlesThisWeek: number;
  sessionCount: number;
  avgSessionSize: number; // total / sessionCount (0 when there are no reads)
  /** Modal time of day over the ROLLING 30-day window in the reader's local clock; null when the
   *  window holds too few reads to claim a habit (the card renders "—"). */
  preferredTime: TimeBucket | null;
}

/**
 * Behavioural summary of how a reader consumes over time: volume this week, average contiguous-
 * session size, and the modal time-of-day. `now` is injectable for tests. Pure.
 */
export function readingPattern(entries: HistoryEntry[], now: number = Date.now()): ReadingPattern {
  const weekAgo = now - 7 * 24 * 60 * 60 * 1000;
  const articlesThisWeek = entries.filter((e) => parseReadAt(e.readAt).getTime() >= weekAgo).length;

  const byDay = new Map<string, HistoryEntry[]>();
  for (const e of entries) {
    const k = dayKey(e.readAt);
    const arr = byDay.get(k) ?? [];
    arr.push(e);
    byDay.set(k, arr);
  }
  let sessionCount = 0;
  for (const reads of byDay.values()) sessionCount += sessionize(reads).length;

  // Preferred time is a CURRENT-habit signal: rolling 30-day window, reader-local clock, with a
  // minimum-sample floor. (`total` / `sessionCount` deliberately stay over the whole filtered set —
  // they are volume facts, not habit claims.)
  const preferredTime = preferredTimeBucket(entries, { now });

  return {
    total: entries.length,
    articlesThisWeek,
    sessionCount,
    avgSessionSize: sessionCount ? entries.length / sessionCount : 0,
    preferredTime,
  };
}
