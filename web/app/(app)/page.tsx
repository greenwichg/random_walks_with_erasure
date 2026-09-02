"use client";

import * as React from "react";
import { Newspaper } from "lucide-react";
import { useDashboard, useDiscover, useRecommendations, useStories } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { PageGrid } from "@/components/layout/page-grid";
import { SectionHeader } from "@/components/shared/section-header";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";
import { TrendingTopicsRail } from "@/components/home/trending-topics-rail";
import { DailyBriefing } from "@/components/home/daily-briefing";
import { HeroStory } from "@/components/home/hero-story";
import { StoryListItem } from "@/components/home/story-list-item";
import { StoryFeatureCard } from "@/components/home/story-feature-card";
import { CategorySection } from "@/components/home/category-section";
import { RecommendationPanel } from "@/components/home/recommendation-panel";
import { InformationHealthPanel } from "@/components/home/information-health-panel";
import { LocalPulse } from "@/components/home/local-pulse";
import { PublisherSpotlight } from "@/components/home/publisher-spotlight";
import { HomeSkeleton } from "@/components/home/home-skeleton";
import { BlindspotModule } from "@/components/home/blindspot-module";
import { TopicArticlesSection } from "@/components/home/topic-articles-section";
import { CoverageSnapshot, TrendingTopicsPanel } from "@/components/home/rail-modules";
import {
  blindspotStories,
  briefingFacts,
  coverageMix,
  coverageUrlKeys,
  freshArticles,
  groupByTopic,
  isoHoursAgo,
  latestStories,
  mergeStories,
  publisherStats,
  topicTier,
  trendingTopics,
  withTopicCount,
  TOPIC_ARTICLE_LIMIT,
  TOPIC_TARGET_EVENTS,
  TOPIC_TOPUP_DAYS,
  TOPIC_TOPUP_LIMIT,
} from "@ih/core/logic/home";
import { useTranslation } from "@/lib/i18n";

/** How many clustered events back the whole page (hero + top stories + every category module). */
const STORY_PAGE_SIZE = 60;
/** Events listed under "Top stories" before the category modules begin. */
const TOP_STORY_COUNT = 8;
/** Events in the closing "Latest" run — the page's scroll reward. */
const LATEST_COUNT = 8;
/** Catalog articles asked for under a thin topic, before `freshArticles` selects from them. Wider
 *  than the slots so the age limit, the coverage dedup and the per-outlet cap have room to work. */
const TOPIC_ARTICLE_FETCH = 40;

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
 *
 * TOPIC VIEWS (HOME2). That one page is the day's top sixty events across every topic, so a chip's
 * local slice can be one or two events for a topic the catalog covers deeply. When a slice falls
 * short of `TOPIC_TARGET_EVENTS`, the page asks the same Story Service for that topic's events
 * over the last `TOPIC_TOPUP_DAYS` days and merges them behind the page's own (`mergeStories`);
 * when the view is still thin, it adds the topic's recent single-outlet articles from the catalog
 * (`freshArticles`: dated, not already a story member above, two per outlet, newest first). Both
 * requests are conditional on thinness, so a rich topic costs exactly what it did before.
 */
export default function HomePage() {
  const { t } = useTranslation();
  const stories = useStories({ sort: "top", limit: STORY_PAGE_SIZE });
  const dashboard = useDashboard();
  const recommendations = useRecommendations();

  const [topic, setTopic] = React.useState<string | null>(null);

  const all = React.useMemo(() => stories.data?.stories ?? [], [stories.data]);
  const local = React.useMemo(
    () => (topic ? all.filter((s) => s.topic === topic) : all),
    [all, topic],
  );

  // --- Topic top-up: more of the SAME thing (events under this topic), only when the page's own
  // slice is thin. `since` is truncated to the hour so the query key is stable across renders.
  const wantsTopUp = !!topic && local.length < TOPIC_TARGET_EVENTS;
  // eslint-disable-next-line react-hooks/exhaustive-deps -- re-anchored per topic selection on purpose
  const since = React.useMemo(() => isoHoursAgo(TOPIC_TOPUP_DAYS * 24), [topic]);
  const topUp = useStories(
    { topic: topic ?? undefined, sort: "top", limit: TOPIC_TOPUP_LIMIT, dateFrom: since },
    { enabled: wantsTopUp },
  );
  const visible = React.useMemo(
    () => (wantsTopUp ? mergeStories(local, topUp.data?.stories ?? []) : local),
    [wantsTopUp, local, topUp.data],
  );
  const tier = topic ? topicTier(visible.length) : "full";
  const topUpPending = wantsTopUp && topUp.isLoading;

  // --- Fresh single-outlet articles: only once the view is known to be thin after the top-up.
  const wantsArticles = !!topic && !topUpPending && tier !== "full";
  const catalog = useDiscover(
    { topic: topic ?? undefined, limit: TOPIC_ARTICLE_FETCH },
    { enabled: wantsArticles },
  );
  const articles = React.useMemo(
    () =>
      wantsArticles
        ? freshArticles(catalog.data?.articles ?? [], {
            exclude: coverageUrlKeys(visible),
            limit: TOPIC_ARTICLE_LIMIT,
          })
        : [],
    [wantsArticles, catalog.data, visible],
  );

  // The rail always offers every topic in the payload, so a filter can never strand the reader
  // with no way back to the full set. The active chip's count follows the merged view.
  const rail = React.useMemo(
    () => withTopicCount(trendingTopics(all), topic, visible.length),
    [all, topic, visible.length],
  );

  const facts = React.useMemo(() => briefingFacts(visible), [visible]);
  const publishers = React.useMemo(() => publisherStats(visible, 6), [visible]);

  const hero = visible[0];
  const topStories = React.useMemo(() => visible.slice(1, 1 + TOP_STORY_COUNT), [visible]);
  // The lead already states its own coverage gap on the hero card; listing it again under Blind
  // spots is the one repeat this page never wants — and in a sparse topic view it was the whole
  // module (observed: one Technology event, shown twice on one screen).
  const blindspots = React.useMemo(
    () => blindspotStories(hero ? visible.filter((s) => s.id !== hero.id) : visible, 4),
    [visible, hero],
  );
  const mix = React.useMemo(() => coverageMix(visible), [visible]);

  // Downstream modules exclude only the LEAD. A topic section and a recency run are different
  // editorial lenses on the same day, not a queue to be consumed — excluding everything already
  // shown starved them to nothing on a small corpus (which is exactly what happened: 12 events in,
  // no category modules out). Overlap between "most covered" and "most recent" is normal on a
  // front page; repeating the single most prominent story is not, so the hero stays excluded.
  //
  // In a TOPIC view there is one topic, so a category module would re-list the top stories under
  // a second heading (observed: the Arts view showed the same two events twice). The topic view
  // renders "More in {topic}" instead — the events beyond the top-stories tier, none repeated.
  const categories = React.useMemo(
    () => (topic ? [] : groupByTopic(visible, { exclude: hero ? [hero.id] : [], minStories: 2 })),
    [visible, hero, topic],
  );
  const moreInTopic = React.useMemo(
    () => (topic ? visible.slice(1 + TOP_STORY_COUNT) : []),
    [visible, topic],
  );
  const latest = React.useMemo(() => {
    // Skip the lead and the first few top rows so the closing run still feels like new ground.
    const shown = new Set<string>(visible.slice(0, 5).map((s) => s.id));
    return latestStories(visible, LATEST_COUNT, shown);
  }, [visible]);

  return (
    <PageContainer>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t("home.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("home.subtitle")}</p>
      </div>

      <div className="mb-5">
        <TrendingTopicsRail topics={rail} active={topic} onSelect={setTopic} />
      </div>

      {stories.isLoading && <HomeSkeleton />}
      {stories.isError && <ErrorState onRetry={() => stories.refetch()} />}

      {!stories.isLoading && !stories.isError && visible.length === 0 && !topUpPending && (
        <EmptyState icon={Newspaper} title={t("home.empty.title")} description={t("home.empty.body")} />
      )}

      {(visible.length > 0 || topUpPending) && (
        <PageGrid
          rail={
            /* Companion rail, reader-first: what to read next, then how your diet looks, then the
               day's shape, then browsable indexes. Every module reads from data already fetched. */
            <>
              {recommendations.data && <RecommendationPanel recs={recommendations.data} />}
              <LocalPulse />
              {dashboard.data && <InformationHealthPanel data={dashboard.data} />}
              <CoverageSnapshot mix={mix} events={visible.length} />
              <PublisherSpotlight publishers={publishers} />
              <TrendingTopicsPanel topics={rail} active={topic} onSelect={setTopic} />
            </>
          }
        >
          <DailyBriefing facts={facts} />

            {hero && <HeroStory story={hero} />}

            {/* The top-up is in flight: hold the space the events will take, so a thin topic
                does not flash empty and then jump. */}
            {topUpPending && (
              <div className="grid gap-4 sm:grid-cols-2" aria-hidden>
                <Skeleton className="h-64 w-full rounded-lg" />
                <Skeleton className="h-64 w-full rounded-lg" />
              </div>
            )}

            {topStories.length > 0 && (
              <section aria-labelledby="top-stories-heading">
                <SectionHeader
                  id="top-stories-heading"
                  title={t("home.topStories.title")}
                  href="/stories"
                  actionLabel={t("home.viewAll")}
                />
                {/* Lead pair, then a ranked tail: a section gets a first tier and a second tier
                    instead of eight identical rows. A single remaining event is a full-width
                    summary row rather than a lone half-width card beside empty space. */}
                {topStories.length === 1 && topStories[0] ? (
                  <ul className="divide-y">
                    <StoryListItem story={topStories[0]} rank={2} showImage />
                  </ul>
                ) : (
                  <>
                    <div className="grid gap-4 sm:grid-cols-2">
                      {topStories.slice(0, 2).map((story) => (
                        <StoryFeatureCard key={story.id} story={story} />
                      ))}
                    </div>
                    {topStories.length > 2 && (
                      <ul className="mt-2 divide-y">
                        {topStories.slice(2).map((story, i) => (
                          <StoryListItem key={story.id} story={story} rank={i + 3} showImage />
                        ))}
                      </ul>
                    )}
                  </>
                )}
              </section>
            )}

            {/* In a SPARSE topic the articles are the page's second tier, so they sit right under
                the events; in a THIN one they follow the blind spots and the deeper event list. */}
            {topic && tier === "sparse" && (
              <TopicArticlesSection topic={topic} articles={articles} loading={wantsArticles && catalog.isLoading} />
            )}

            <BlindspotModule stories={blindspots} />

            {topic && moreInTopic.length > 0 && (
              <section aria-labelledby="more-in-topic-heading" className="cv-section">
                <SectionHeader
                  id="more-in-topic-heading"
                  title={t("home.topic.moreTitle", { topic })}
                  eyebrow={t("home.category.eyebrow")}
                  href="/stories"
                  actionLabel={t("home.viewAll")}
                />
                <ul className="divide-y">
                  {moreInTopic.map((story) => (
                    <StoryListItem key={story.id} story={story} variant="compact" showTopic={false} showSplit />
                  ))}
                </ul>
              </section>
            )}

            {topic && tier === "thin" && (
              <TopicArticlesSection topic={topic} articles={articles} loading={wantsArticles && catalog.isLoading} />
            )}

            {/* Alternating feature placement gives consecutive topic modules a magazine rhythm. */}
            {categories.map((group, i) => (
              /* R3: every category section starts below the fold — cv-section defers its layout
                 and paint until the reader scrolls toward it. */
              <div key={group.topic} className="cv-section">
                <CategorySection group={group} limit={6} flip={i % 2 === 1} />
              </div>
            ))}

            {latest.length > 0 && (
              <section aria-labelledby="latest-heading" className="cv-section">
                <SectionHeader
                  id="latest-heading"
                  title={t("home.latest.title")}
                  href="/stories"
                  actionLabel={t("home.viewAll")}
                />
                {/* Compact type on purpose (the closing run contrasts with the image-heavy
                    sections above), but FULL stats: the labelled L/C/R split + publisher count
                    match Top Stories' information density — same payload, same numbers. */}
                <ul className="divide-y">
                  {latest.map((story) => (
                    <StoryListItem key={story.id} story={story} variant="compact" showSplit />
                  ))}
                </ul>
              </section>
            )}
        </PageGrid>
      )}
    </PageContainer>
  );
}
