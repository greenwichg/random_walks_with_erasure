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
  /** Fractions (0..1) of reads in each lean bucket; all 0 when there are no reads. */
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

  const leanCounts = { left: 0, center: 0, right: 0 };
  for (const a of arts) leanCounts[bucketOf(a.lean)]++;
  const leanShare = {
    left: share(leanCounts.left),
    center: share(leanCounts.center),
    right: share(leanCounts.right),
  };

  const reportingShare = share(arts.filter((a) => a.register === "reporting").length);
  const opinionShare = share(arts.filter((a) => a.register === "opinion").length);
  const avgReadingMinutes = count
    ? arts.reduce((s, a) => s + (a.readingMinutes || 0), 0) / count
    : 0;

  const emo = new Map<keyof EmotionShare, number>();
  for (const a of arts) {
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
