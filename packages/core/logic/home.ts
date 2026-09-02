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
import type { Article, Story, ViewpointDistribution } from "../domain/types.ts";

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

/**
 * The blind-spot module: events the Story Service flagged as covered mainly from one side.
 *
 * This is the product's own signal (`blindspotSide`), not a heuristic invented here — the same
 * flag `StoryCard` already renders. Ordered by breadth of coverage, so the most consequential
 * one-sided events lead.
 */
export function blindspotStories(stories: Story[], limit = 4): Story[] {
  return stories
    .filter((s) => !!s.blindspotSide)
    .sort((a, b) => b.totalCoverage - a.totalCoverage || a.id.localeCompare(b.id))
    .slice(0, Math.max(0, limit));
}

/** Newest-first by each event's own latest update. Events with no timestamp sort last. */
export function latestStories(stories: Story[], limit = 8, exclude?: Iterable<string>): Story[] {
  const skip = new Set(exclude ?? []);
  return stories
    .filter((s) => !skip.has(s.id))
    .slice()
    .sort((a, b) => (updatedOf(b) ?? "").localeCompare(updatedOf(a) ?? ""))
    .slice(0, Math.max(0, limit));
}

/**
 * The aggregate left/centre/right split across every event on the page — "how is today being
 * covered overall", summed from the same per-story distributions the spectrum bars render.
 * Summed, never averaged: an event with 40 articles should weigh more than one with 2.
 */
export function coverageMix(stories: Story[]): ViewpointDistribution {
  const mix: ViewpointDistribution = { left: 0, center: 0, right: 0 };
  for (const story of stories) {
    const d = story.distribution;
    if (!d) continue;
    mix.left += Number(d.left) || 0;
    mix.center += Number(d.center) || 0;
    mix.right += Number(d.right) || 0;
  }
  return mix;
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

// ---------------------------------------------------------------------------------------------
// Topic views (HOME2) — completing a category page without inventing content.
//
// The page holds ONE `/api/stories` page: the day's top events across every topic. A topic chip
// slices that page locally, so "Technology · 1" means one of the top sixty events was Technology,
// not that the catalog holds one Technology event. A thin slice is therefore usually an artefact
// of the page size, and the honest fix is to ASK for more of the same thing — events filed under
// that topic, from the same Story Service, over a short recent window — and, when clusters are
// genuinely scarce, to show the topic's recent single-outlet articles, which the catalog holds
// even when nothing has corroborated them into a story yet. Nothing here pads: every item is a
// real event or a real article, deduplicated against what the page already shows, and every
// threshold is a named constant.
// ---------------------------------------------------------------------------------------------

/** A topic view aims for at least this many events before it is left as it is. */
export const TOPIC_TARGET_EVENTS = 8;
/** How many topic-scoped events one top-up request asks the Story Service for. */
export const TOPIC_TOPUP_LIMIT = 24;
/** How far back the top-up looks. Wide enough to find the events the top-sixty page skipped,
 *  short enough to sit on a page called "Today" — three days, with each card dated. */
export const TOPIC_TOPUP_DAYS = 3;
/** Single-outlet articles shown under a thin topic. Two columns, four rows at most. */
export const TOPIC_ARTICLE_LIMIT = 8;
/** An article older than this is not "latest", however few we have. */
export const TOPIC_ARTICLE_MAX_AGE_HOURS = 72;
/** No outlet fills more than this many of the article slots — breadth over one wire's output. */
export const TOPIC_ARTICLE_PER_PUBLISHER = 2;
/** A topic view with this many events or fewer is SPARSE: articles carry the page. */
export const TOPIC_SPARSE_EVENTS = 2;

export type TopicTier = "sparse" | "thin" | "full";

/** How complete a topic view is, by its event count AFTER any top-up. */
export function topicTier(eventCount: number): TopicTier {
  if (eventCount <= TOPIC_SPARSE_EVENTS) return "sparse";
  if (eventCount < TOPIC_TARGET_EVENTS) return "thin";
  return "full";
}

/** The page's own events first, in their ranked order, then the top-up's events that were not
 *  already on the page. Deduplicated by story id, so a merge can never show one event twice. */
export function mergeStories(primary: Story[], extra: Story[]): Story[] {
  const seen = new Set<string>();
  const out: Story[] = [];
  for (const s of [...primary, ...extra]) {
    if (!s?.id || seen.has(s.id)) continue;
    seen.add(s.id);
    out.push(s);
  }
  return out;
}

/** A URL reduced to what identifies the article: host without `www.`, path without a trailing
 *  slash, no scheme, query or fragment. Coverage rows and catalog articles carry the same
 *  publisher URL, but one may have `?utm_…` or `http://` the other lacks. */
export function urlKey(url: string | null | undefined): string {
  if (!url) return "";
  let u = String(url).trim().toLowerCase();
  u = u.replace(/^https?:\/\//, "").replace(/^www\./, "");
  const cut = u.search(/[?#]/);
  if (cut >= 0) u = u.slice(0, cut);
  return u.replace(/\/+$/, "");
}

/** Every article URL the given stories already show as coverage — what a "latest articles" list
 *  must exclude so it never repeats a story's own members as new items. */
export function coverageUrlKeys(stories: Story[]): Set<string> {
  const keys = new Set<string>();
  for (const s of stories) {
    for (const row of s.coverage ?? []) {
      const k = urlKey(row.url);
      if (k) keys.add(k);
    }
  }
  return keys;
}

export interface FreshArticlesOptions {
  /** URL keys to leave out — normally `coverageUrlKeys(visible stories)`. */
  exclude?: Set<string>;
  /** The clock, injectable so the selection is testable. */
  now?: Date | string;
  maxAgeHours?: number;
  limit?: number;
  perPublisher?: number;
}

/**
 * The topic's recent single-outlet articles, newest first: dated within the age limit, not already
 * shown as a story's coverage, and at most a few per outlet so one busy wire cannot fill every
 * slot. An article with no parseable publication time is left out — its age is unknown, and
 * "unknown" is not "fresh".
 */
export function freshArticles(articles: Article[], options: FreshArticlesOptions = {}): Article[] {
  const {
    exclude,
    now = new Date(),
    maxAgeHours = TOPIC_ARTICLE_MAX_AGE_HOURS,
    limit = TOPIC_ARTICLE_LIMIT,
    perPublisher = TOPIC_ARTICLE_PER_PUBLISHER,
  } = options;
  const nowMs = typeof now === "string" ? Date.parse(now) : now.getTime();
  const oldest = nowMs - maxAgeHours * 3_600_000;

  const dated: { article: Article; at: number }[] = [];
  const seenUrl = new Set<string>();
  for (const a of articles) {
    const at = Date.parse(a.publishedAt ?? "");
    if (!Number.isFinite(at) || at < oldest || at > nowMs + 600_000) continue;
    const key = urlKey(a.url);
    if (key && (exclude?.has(key) || seenUrl.has(key))) continue;
    if (key) seenUrl.add(key);
    dated.push({ article: a, at });
  }
  dated.sort((x, y) => y.at - x.at || x.article.id.localeCompare(y.article.id));

  const perOutlet = new Map<string, number>();
  const out: Article[] = [];
  for (const { article } of dated) {
    if (out.length >= Math.max(0, limit)) break;
    const outlet = (article.publisher ?? "").trim().toLowerCase();
    const used = perOutlet.get(outlet) ?? 0;
    if (outlet && used >= Math.max(1, perPublisher)) continue;
    perOutlet.set(outlet, used + 1);
    out.push(article);
  }
  return out;
}

/** The rail with the ACTIVE chip's count replaced by what the view actually shows after a top-up,
 *  so a chip never says "1" beside nine stories. Other chips keep their page-derived counts, and
 *  a topic the rail does not offer is never added — the rail is still what the page holds. */
export function withTopicCount(rail: TopicCount[], topic: string | null, count: number): TopicCount[] {
  if (!topic) return rail;
  return rail.map((entry) => (entry.topic === topic ? { topic, count: Math.max(entry.count, count) } : entry));
}

/** An ISO timestamp `hours` before `now`, truncated to the hour. Truncation is what keeps a
 *  query key stable across re-renders within the same hour — a `dateFrom` that changed every
 *  millisecond would be a fresh cache entry, and a fresh request, on every render. */
export function isoHoursAgo(hours: number, now: Date = new Date()): string {
  const t = new Date(now.getTime() - hours * 3_600_000);
  t.setUTCMinutes(0, 0, 0);
  return t.toISOString();
}
