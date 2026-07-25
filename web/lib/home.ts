/**
 * Home-page derivations (HOME1) — the pure, testable functions that turn ONE `/api/stories` page
 * into every section the home page renders: the briefing facts, the hero, the top-stories list,
 * the per-topic category modules, the trending rail, and the publisher spotlight.
 *
 * Why derive instead of fetch-per-section: the Story Service already returns the clustered events
 * with their publishers, distribution, topic and freshness attached, so a second request per
 * section would re-ask the engine for data we already hold. One query, many sections — fewer
 * round-trips, one cache entry, and no section can ever disagree with another.
 *
 * Honest by construction (the house rule): every number here is COUNTED from the payload — nothing
 * is estimated, padded, or synthesized. A topic with too little coverage simply doesn't become a
 * section, and an empty payload yields empty results rather than placeholder content.
 *
 * No React, no imports beyond types — runs under `node --test`.
 */
import type { Story } from "../types/domain";

/** Distinct publishers named by a story — the `publishers` list when the engine sent one, else the
 *  coverage rows (a story always carries coverage; `publishers` is a newer convenience field). */
function publishersOf(story: Story): string[] {
  const named = (story.publishers ?? []).filter((p): p is string => !!p && !!p.trim());
  if (named.length) return named;
  return (story.coverage ?? []).map((c) => c.publisher).filter((p): p is string => !!p && !!p.trim());
}

/** The most recent update timestamp a story carries (ISO strings compare lexicographically). */
function updatedOf(story: Story): string | null {
  return story.latestUpdate || story.updatedAt || story.newest || null;
}

/** Counted facts behind the "Today's briefing" module. All four are real counts over the payload. */
export interface BriefingFacts {
  /** Clustered events in this page of coverage. */
  storyCount: number;
  /** Distinct publishers across those events. */
  publisherCount: number;
  /** Events the Story Service flagged as thin on one side (`blindspotSide`). */
  blindspotCount: number;
  /** The newest update timestamp across the payload, or null when nothing carries one. */
  latestUpdate: string | null;
}

export function briefingFacts(stories: Story[]): BriefingFacts {
  const publishers = new Set<string>();
  let blindspotCount = 0;
  let latestUpdate: string | null = null;

  for (const story of stories) {
    for (const p of publishersOf(story)) publishers.add(p);
    if (story.blindspotSide) blindspotCount += 1;
    const at = updatedOf(story);
    if (at && (latestUpdate === null || at > latestUpdate)) latestUpdate = at;
  }

  return {
    storyCount: stories.length,
    publisherCount: publishers.size,
    blindspotCount,
    latestUpdate,
  };
}

/** One category module: a real catalog topic and the events filed under it. */
export interface TopicGroup {
  topic: string;
  stories: Story[];
}

export interface GroupByTopicOptions {
  /** A topic needs at least this many events to earn a section (no thin, padded modules). */
  minStories?: number;
  /** Cap on how many category modules the page renders. */
  maxGroups?: number;
  /** Story ids already shown above (hero / top stories) so a module never repeats them. */
  exclude?: Iterable<string>;
}

/**
 * Group events into category modules by their real catalog topic. Topics are NOT a hardcoded list
 * (Politics / Business / …) — they come from whatever the catalog actually carries, so the page
 * reflects the live corpus instead of asserting sections that may be empty. Ordered by coverage
 * depth, then alphabetically, so the ordering is deterministic for identical input.
 */
export function groupByTopic(stories: Story[], options: GroupByTopicOptions = {}): TopicGroup[] {
  const { minStories = 3, maxGroups = 6, exclude } = options;
  const skip = new Set(exclude ?? []);
  const byTopic = new Map<string, Story[]>();

  for (const story of stories) {
    if (skip.has(story.id)) continue;
    const topic = (story.topic ?? "").trim();
    if (!topic) continue; // an unclassified event is never filed under a guessed topic
    const bucket = byTopic.get(topic);
    if (bucket) bucket.push(story);
    else byTopic.set(topic, [story]);
  }

  return [...byTopic.entries()]
    .filter(([, list]) => list.length >= minStories)
    .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
    .slice(0, maxGroups)
    .map(([topic, list]) => ({ topic, stories: list }));
}

/** A topic and how many of today's events it covers. */
export interface TopicCount {
  topic: string;
  count: number;
}

/** The trending rail: real catalog topics ranked by how much coverage they carry right now. */
export function trendingTopics(stories: Story[], limit = 12): TopicCount[] {
  const counts = new Map<string, number>();
  for (const story of stories) {
    const topic = (story.topic ?? "").trim();
    if (!topic) continue;
    counts.set(topic, (counts.get(topic) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, Math.max(0, limit))
    .map(([topic, count]) => ({ topic, count }));
}

/** A publisher and how many of today's events they appear in. */
export interface PublisherCount {
  publisher: string;
  stories: number;
}

/**
 * The publisher spotlight: who is actually carrying today's coverage, counted from the payload.
 * Deliberately NOT a curated masthead list — the product never asserts a publisher it can't see.
 */
export function publisherStats(stories: Story[], limit = 8): PublisherCount[] {
  const counts = new Map<string, number>();
  for (const story of stories) {
    for (const publisher of new Set(publishersOf(story))) {
      counts.set(publisher, (counts.get(publisher) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, Math.max(0, limit))
    .map(([publisher, count]) => ({ publisher, stories: count }));
}
