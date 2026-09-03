"use client";

import * as React from "react";
import type { Story, ViewpointDistribution } from "@ih/core/domain/types";
import {
  blindspotStories,
  briefingFacts,
  coverageMix,
  groupByTopic,
  latestStories,
  publisherStats,
  trendingTopics,
  type BriefingFacts,
  type PublisherCount,
  type TopicCount,
  type TopicGroup,
} from "@ih/core/logic/home";

/** How many clustered events back the whole page (hero + top stories + every category module). */
export const STORY_PAGE_SIZE = 60;
/** Events listed under "Top stories" before the category modules begin. */
const TOP_STORY_COUNT = 8;
/** Events in the closing "Latest" run — the page's scroll reward. */
const LATEST_COUNT = 8;

/** Everything both home compositions render, derived ONCE from the single `/api/stories` page. */
export interface HomeModel {
  rail: TopicCount[];
  topic: string | null;
  setTopic: (topic: string | null) => void;
  visible: Story[];
  facts: BriefingFacts;
  publishers: PublisherCount[];
  hero: Story | undefined;
  topStories: Story[];
  blindspots: Story[];
  mix: ViewpointDistribution;
  categories: TopicGroup[];
  latest: Story[];
}

/**
 * The home page's derived model — the memoised selections that used to live inline in
 * app/(app)/page.tsx, shared by the desktop front page and the mobile page so the two can never
 * disagree about which story is the lead or which topics earn a section.
 *
 * The rail always offers every topic in the payload, so a filter can never strand the reader
 * with no way back to the full set. Downstream modules exclude only the LEAD: a topic section
 * and a recency run are different editorial lenses on the same day, not a queue to be consumed.
 */
export function useHomeModel(all: Story[]): HomeModel {
  const [topic, setTopic] = React.useState<string | null>(null);
  const rail = React.useMemo(() => trendingTopics(all), [all]);
  const visible = React.useMemo(() => (topic ? all.filter((s) => s.topic === topic) : all), [all, topic]);

  const facts = React.useMemo(() => briefingFacts(visible), [visible]);
  const publishers = React.useMemo(() => publisherStats(visible, 6), [visible]);

  const hero = visible[0];
  const topStories = React.useMemo(() => visible.slice(1, 1 + TOP_STORY_COUNT), [visible]);
  const blindspots = React.useMemo(() => blindspotStories(visible, 4), [visible]);
  const mix = React.useMemo(() => coverageMix(visible), [visible]);
  const categories = React.useMemo(
    () => groupByTopic(visible, { exclude: hero ? [hero.id] : [], minStories: 2 }),
    [visible, hero],
  );
  const latest = React.useMemo(() => {
    // Skip the lead and the first few top rows so the closing run still feels like new ground.
    const shown = new Set<string>(visible.slice(0, 5).map((s) => s.id));
    return latestStories(visible, LATEST_COUNT, shown);
  }, [visible]);

  return { rail, topic, setTopic, visible, facts, publishers, hero, topStories, blindspots, mix, categories, latest };
}
