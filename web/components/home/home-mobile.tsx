"use client";

import * as React from "react";
import { Newspaper } from "lucide-react";
import type { DashboardSummary, Recommendation, Story } from "@ih/core/domain/types";
import { PageContainer } from "@/components/layout/page-container";
import { PageGrid } from "@/components/layout/page-grid";
import { SectionHeader } from "@/components/shared/section-header";
import { EmptyState, ErrorState } from "@/components/shared/states";
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
import { CoverageSnapshot, TrendingTopicsPanel } from "@/components/home/rail-modules";
import type { HomeModel } from "@/components/home/home-model";
import { useTranslation } from "@/lib/i18n";

/**
 * The home page below `lg` — the composition as it was before the desktop front page existed,
 * moved verbatim out of app/(app)/page.tsx so the desktop rework could not touch it. The page
 * picks this tree or the desktop one from the viewport (lib/use-is-desktop.ts); both read the
 * same derived model, so nothing is fetched twice.
 */
export function HomeMobile({
  model,
  dashboard,
  recommendations,
  loading,
  error,
  onRetry,
}: {
  model: HomeModel;
  dashboard: DashboardSummary | undefined;
  recommendations: Recommendation[] | undefined;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  const { rail, topic, setTopic, visible, facts, publishers, hero, topStories, blindspots, mix, categories, latest } =
    model;

  return (
    <PageContainer>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t("home.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("home.subtitle")}</p>
      </div>

      <div className="mb-5">
        <TrendingTopicsRail topics={rail} active={topic} onSelect={setTopic} />
      </div>

      {loading && <HomeSkeleton />}
      {error && <ErrorState onRetry={onRetry} />}

      {!loading && !error && visible.length === 0 && (
        <EmptyState icon={Newspaper} title={t("home.empty.title")} description={t("home.empty.body")} />
      )}

      {visible.length > 0 && (
        <PageGrid
          rail={
            /* Companion rail, reader-first: what to read next, then how your diet looks, then the
               day's shape, then browsable indexes. Every module reads from data already fetched. */
            <>
              {recommendations && <RecommendationPanel recs={recommendations} />}
              <LocalPulse />
              {dashboard && <InformationHealthPanel data={dashboard} />}
              <CoverageSnapshot mix={mix} events={visible.length} />
              <PublisherSpotlight publishers={publishers} />
              <TrendingTopicsPanel topics={rail} active={topic} onSelect={setTopic} />
            </>
          }
        >
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
                {/* Lead pair, then a ranked tail: a section gets a first tier and a second tier
                    instead of eight identical rows. */}
                <div className="grid gap-4 sm:grid-cols-2">
                  {topStories.slice(0, 2).map((story: Story) => (
                    <StoryFeatureCard key={story.id} story={story} />
                  ))}
                </div>
                {topStories.length > 2 && (
                  <ul className="mt-2 divide-y">
                    {topStories.slice(2).map((story: Story, i: number) => (
                      <StoryListItem key={story.id} story={story} rank={i + 3} showImage />
                    ))}
                  </ul>
                )}
              </section>
            )}

            <BlindspotModule stories={blindspots} />

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
                  {latest.map((story: Story) => (
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
