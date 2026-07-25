"use client";

import * as React from "react";
import { Newspaper } from "lucide-react";
import { useDashboard, useRecommendations, useStories } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { SectionHeader } from "@/components/shared/section-header";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { UtilityBar } from "@/components/home/utility-bar";
import { TrendingTopicsRail } from "@/components/home/trending-topics-rail";
import { DailyBriefing } from "@/components/home/daily-briefing";
import { HeroStory } from "@/components/home/hero-story";
import { StoryListItem } from "@/components/home/story-list-item";
import { CategorySection } from "@/components/home/category-section";
import { RecommendationPanel } from "@/components/home/recommendation-panel";
import { InformationHealthPanel } from "@/components/home/information-health-panel";
import { PublisherSpotlight } from "@/components/home/publisher-spotlight";
import { SiteFooter } from "@/components/home/site-footer";
import { HomeSkeleton } from "@/components/home/home-skeleton";
import { BlindspotModule } from "@/components/home/blindspot-module";
import {
  BlindspotSummary,
  CoverageSnapshot,
  TrendingTopicsPanel,
} from "@/components/home/rail-modules";
import {
  blindspotStories,
  briefingFacts,
  coverageMix,
  groupByTopic,
  latestStories,
  publisherStats,
  trendingTopics,
} from "@/lib/home";
import { useTranslation } from "@/lib/i18n";

/** How many clustered events back the whole page (hero + top stories + every category module). */
const STORY_PAGE_SIZE = 60;
/** Events listed under "Top stories" before the category modules begin. */
const TOP_STORY_COUNT = 8;
/** Events in the closing "Latest" run — the page's scroll reward. */
const LATEST_COUNT = 8;

/**
 * The Hidden View home page — a news-intelligence front page.
 *
 * ONE `/api/stories` request drives the lead, the top-stories list, the category modules, the
 * trending rail and the publisher spotlight (see `lib/home.ts`); the reader's own Information
 * Health and recommendation feed ride alongside it in the rail. Three queries in total, all of
 * them endpoints that already existed — no new backend surface, no new data contract.
 *
 * The rail's topic chips filter this page in place rather than navigating, so switching topics
 * costs no request and cannot disagree with what is already on screen.
 */
export default function HomePage() {
  const { t } = useTranslation();
  const stories = useStories({ sort: "top", limit: STORY_PAGE_SIZE });
  const dashboard = useDashboard();
  const recommendations = useRecommendations();

  const [topic, setTopic] = React.useState<string | null>(null);

  const all = React.useMemo(() => stories.data?.stories ?? [], [stories.data]);
  // The rail always offers every topic in the payload, so a filter can never strand the reader
  // with no way back to the full set.
  const rail = React.useMemo(() => trendingTopics(all), [all]);
  const visible = React.useMemo(
    () => (topic ? all.filter((s) => s.topic === topic) : all),
    [all, topic],
  );

  const facts = React.useMemo(() => briefingFacts(visible), [visible]);
  const publishers = React.useMemo(() => publisherStats(visible, 6), [visible]);

  const hero = visible[0];
  const topStories = React.useMemo(() => visible.slice(1, 1 + TOP_STORY_COUNT), [visible]);
  const blindspots = React.useMemo(() => blindspotStories(visible, 4), [visible]);
  const mix = React.useMemo(() => coverageMix(visible), [visible]);
  const flagged = React.useMemo(() => visible.filter((s) => !!s.blindspotSide).length, [visible]);

  // Downstream modules exclude only the LEAD. A topic section and a recency run are different
  // editorial lenses on the same day, not a queue to be consumed — excluding everything already
  // shown starved them to nothing on a small corpus (which is exactly what happened: 12 events in,
  // no category modules out). Overlap between "most covered" and "most recent" is normal on a
  // front page; repeating the single most prominent story is not, so the hero stays excluded.
  const categories = React.useMemo(
    () => groupByTopic(visible, { exclude: hero ? [hero.id] : [], minStories: 2 }),
    [visible, hero],
  );
  const latest = React.useMemo(() => {
    // Skip the lead and the first few top rows so the closing run still feels like new ground.
    const shown = new Set<string>(visible.slice(0, 5).map((s) => s.id));
    return latestStories(visible, LATEST_COUNT, shown);
  }, [visible]);

  return (
    <PageContainer>
      <UtilityBar />

      <div className="mb-3 mt-4 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t("home.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("home.subtitle")}</p>
      </div>

      <div className="mb-5">
        <TrendingTopicsRail topics={rail} active={topic} onSelect={setTopic} />
      </div>

      {stories.isLoading && <HomeSkeleton />}
      {stories.isError && <ErrorState onRetry={() => stories.refetch()} />}

      {!stories.isLoading && !stories.isError && visible.length === 0 && (
        <EmptyState icon={Newspaper} title={t("home.empty.title")} description={t("home.empty.body")} />
      )}

      {visible.length > 0 && (
        <div className="grid grid-cols-12 gap-x-8 gap-y-8">
          {/* ---- Lead column ---- */}
          <div className="col-span-12 space-y-8 lg:col-span-8">
            <DailyBriefing facts={facts} />

            {hero && <HeroStory story={hero} />}

            {topStories.length > 0 && (
              <section aria-labelledby="top-stories-heading">
                <SectionHeader
                  id="top-stories-heading"
                  title={t("home.topStories.title")}
                  href="/stories"
                  actionLabel={t("home.viewAll")}
                />
                <ul className="divide-y">
                  {topStories.map((story, i) => (
                    <StoryListItem key={story.id} story={story} rank={i + 1} showImage />
                  ))}
                </ul>
              </section>
            )}

            <BlindspotModule stories={blindspots} />

            {categories.map((group) => (
              <CategorySection key={group.topic} group={group} limit={6} />
            ))}

            {latest.length > 0 && (
              <section aria-labelledby="latest-heading">
                <SectionHeader
                  id="latest-heading"
                  title={t("home.latest.title")}
                  href="/stories"
                  actionLabel={t("home.viewAll")}
                />
                <ul className="divide-y">
                  {latest.map((story) => (
                    <StoryListItem key={story.id} story={story} showImage />
                  ))}
                </ul>
              </section>
            )}
          </div>

          {/* ---- Companion rail ----
              Ordered reader-first: what to read next, then how your diet looks, then the day's
              shape, then browsable indexes. Every module below reads from data already fetched. */}
          <aside className="col-span-12 space-y-8 lg:col-span-4">
            {recommendations.data && <RecommendationPanel recs={recommendations.data} />}
            {dashboard.data && <InformationHealthPanel data={dashboard.data} />}
            <CoverageSnapshot mix={mix} events={visible.length} />
            <BlindspotSummary flagged={flagged} total={visible.length} />
            <PublisherSpotlight publishers={publishers} />
            <TrendingTopicsPanel topics={rail} active={topic} onSelect={setTopic} />
          </aside>
        </div>
      )}

      <SiteFooter />
    </PageContainer>
  );
}
