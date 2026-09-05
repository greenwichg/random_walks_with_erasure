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

/** How many clustered events back the whole page. */
export const STORY_PAGE_SIZE = 60;
const TOP_STORY_COUNT = 8;
const LATEST_COUNT = 8;

export interface HomeModel {
  rail: TopicCount[];
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
 * The home page's derived model — the same memoised selections `web/components/home/home-model.ts`
 * makes over the single `/api/stories` page, so the phone and the browser can never disagree about
 * which story is the lead or which topics earn a section. Every derivation is `@ih/core/logic/home`.
 */
export function useHomeModel(all: Story[]): HomeModel {
  const rail = React.useMemo(() => trendingTopics(all), [all]);
  const visible = all;
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
    const shown = new Set<string>(visible.slice(0, 5).map((s) => s.id));
    return latestStories(visible, LATEST_COUNT, shown);
  }, [visible]);

  return { rail, visible, facts, publishers, hero, topStories, blindspots, mix, categories, latest };
}
