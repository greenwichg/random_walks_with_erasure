/**
 * Reading-History insights — a pure, testable aggregation over a set of reads (Phase 1 of the
 * History redesign). It powers the Information Health strip and the Reflection/Insights section:
 * descriptive counts and shares of the reads *currently in view*, deliberately distinct from the
 * engine's scored, all-time Information Health metrics (Dashboard / Report). No React, no i18n —
 * callers map the returned discriminators to catalog strings.
 */
import type { HistoryEntry, EmotionShare } from "@/types/domain";

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

/**
 * Stable local-day key (YYYY-MM-DD) for a timestamp — the shared identifier that syncs the Calendar
 * selection to the Timeline grouping (both bucket reads by local day). Pure; no imports.
 */
export function dayKey(iso: string): string {
  const d = new Date(iso);
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
  const sorted = [...entries].sort((a, b) => new Date(b.readAt).getTime() - new Date(a.readAt).getTime());
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
    const gapMs = new Date(cur[cur.length - 1]!.readAt).getTime() - new Date(e.readAt).getTime();
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

export interface ReadingPattern {
  total: number;
  articlesThisWeek: number;
  sessionCount: number;
  avgSessionSize: number; // total / sessionCount (0 when there are no reads)
  preferredTime: TimeBucket | null;
}

/**
 * Behavioural summary of how a reader consumes over time: volume this week, average contiguous-
 * session size, and the modal time-of-day. `now` is injectable for tests. Pure.
 */
export function readingPattern(entries: HistoryEntry[], now: number = Date.now()): ReadingPattern {
  const weekAgo = now - 7 * 24 * 60 * 60 * 1000;
  const articlesThisWeek = entries.filter((e) => new Date(e.readAt).getTime() >= weekAgo).length;

  const byDay = new Map<string, HistoryEntry[]>();
  for (const e of entries) {
    const k = dayKey(e.readAt);
    const arr = byDay.get(k) ?? [];
    arr.push(e);
    byDay.set(k, arr);
  }
  let sessionCount = 0;
  for (const reads of byDay.values()) sessionCount += sessionize(reads).length;

  const buckets = new Map<TimeBucket, number>();
  for (const e of entries) {
    const bkt = timeBucket(new Date(e.readAt).getHours());
    buckets.set(bkt, (buckets.get(bkt) ?? 0) + 1);
  }
  let preferredTime: TimeBucket | null = null;
  let max = 0;
  for (const [bkt, n] of buckets) if (n > max) { max = n; preferredTime = bkt; }

  return {
    total: entries.length,
    articlesThisWeek,
    sessionCount,
    avgSessionSize: sessionCount ? entries.length / sessionCount : 0,
    preferredTime,
  };
}
