"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, EyeOff, Newspaper, Users } from "lucide-react";
import { useRecommendations, useStories, useStory } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { PageGrid } from "@/components/layout/page-grid";
import { SectionHeader } from "@/components/shared/section-header";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { ArticleImage } from "@/components/shared/article-image";
import { ShareButton } from "@/components/shared/share-button";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";
import { FreshnessBadge } from "@/components/stories/freshness-badge";
import { StoryIntelligencePanel } from "@/components/stories/story-intelligence-panel";
import { CoverageList } from "@/components/stories/coverage-list";
import { StoryCoveragePanel } from "@/components/stories/story-coverage-panel";
import { StoryListItem } from "@/components/home/story-list-item";
import { RecommendationPanel } from "@/components/home/recommendation-panel";
import { PublisherSpotlight } from "@/components/home/publisher-spotlight";
import { LEAN_META } from "@/lib/metrics";
import { useTranslation } from "@/lib/i18n";
import { activeLang, formatDate } from "@/lib/i18n-core";

const fmtDate = (iso?: string) =>
  iso ? formatDate(iso, activeLang(), { month: "short", day: "numeric", year: "numeric" }) : "";

/**
 * Story Details — the same editorial system as the home page, answering the reader's questions in
 * order: what happened (hero + the cluster's real summary as the standfirst), how it's covered
 * (filterable coverage list + the intelligence timeline), where coverage is thin (breakdown panel's
 * zero-side callouts), and what to read next (related stories + the reader's own feed).
 *
 * Three queries: the story, its intelligence (inside StoryIntelligencePanel), and the SAME
 * top-stories page the home page runs — usually a cache hit — for related coverage. The reader's
 * recommendations reuse their existing cached feed. No new endpoints.
 */
export default function StoryDetailPage() {
  const { t, timeAgo } = useTranslation();
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const { data: story, isLoading, isError, refetch } = useStory(id);
  const stories = useStories({ sort: "top", limit: 60 });
  const recommendations = useRecommendations();

  // Related coverage: same topic first, then the day's top events, never this story itself.
  const related = React.useMemo(() => {
    const all = stories.data?.stories ?? [];
    const others = all.filter((s) => s.id !== id);
    const sameTopic = story ? others.filter((s) => s.topic === story.topic) : [];
    const rest = story ? others.filter((s) => s.topic !== story.topic) : others;
    return [...sameTopic, ...rest].slice(0, 4);
  }, [stories.data, story, id]);

  const back = (
    <Link
      href="/stories"
      className="inline-flex items-center gap-1.5 rounded text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
    >
      <ArrowLeft className="h-4 w-4" aria-hidden /> {t("stories.back")}
    </Link>
  );

  if (isLoading) {
    return (
      <PageContainer>
        <div className="mb-5">{back}</div>
        <div aria-hidden>
          <PageGrid
            rail={
              <>
                <Skeleton className="h-48 w-full rounded-lg" />
                <Skeleton className="h-72 w-full rounded-lg" />
              </>
            }
          >
            <Skeleton className="aspect-[21/9] w-full rounded-lg" />
            <Skeleton className="h-40 w-full rounded-lg" />
            <div className="space-y-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-20 rounded-md" />
              ))}
            </div>
          </PageGrid>
        </div>
      </PageContainer>
    );
  }
  if (isError) {
    return (
      <PageContainer>
        <div className="mb-5">{back}</div>
        <ErrorState onRetry={() => refetch()} />
      </PageContainer>
    );
  }
  if (!story) {
    return (
      <PageContainer>
        <div className="mb-5">{back}</div>
        <EmptyState
          icon={Newspaper}
          title={t("stories.notFound.title")}
          description={t("stories.notFound.body")}
        />
      </PageContainer>
    );
  }

  const publisherCount = story.publisherCount ?? new Set(story.coverage.map((c) => c.publisher)).size;
  const publisherCounts = (() => {
    const counts = new Map<string, number>();
    for (const row of story.coverage) counts.set(row.publisher, (counts.get(row.publisher) ?? 0) + 1);
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, 8)
      .map(([publisher, articles]) => ({ publisher, stories: articles }));
  })();

  return (
    <PageContainer>
      {/* Breadcrumb row: the way back on the left, actions on the right. */}
      <div className="mb-5 flex items-center justify-between gap-3">
        {back}
        <ShareButton title={story.title} />
      </div>

      <PageGrid
        rail={
          /* Companion rail: how balanced is THIS story, who's on it, what next for YOU. */
          <>
            <StoryCoveragePanel distribution={story.distribution} coverage={story.coverage} />
            <PublisherSpotlight
              publishers={publisherCounts}
              titleKey="story.publishersTitle"
              countKey="stories.articlesCount"
            />
            {recommendations.data && <RecommendationPanel recs={recommendations.data} />}
          </>
        }
      >
          {/* What happened — the hero, with the cluster's real summary as the standfirst. */}
          <article className="overflow-hidden rounded-lg border bg-card shadow-soft">
            <ArticleImage
              src={story.image}
              alt={story.title}
              aspect="aspect-[21/9]"
              className="w-full rounded-none border-0"
            />
            <div className="p-5">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground">
                  {story.topic}
                </span>
                {story.freshness && (
                  <FreshnessBadge band={story.freshness.band} score={story.freshness.score} />
                )}
              </div>

              <h1 className="text-balance text-2xl font-semibold leading-tight tracking-tight sm:text-3xl">
                {story.title}
              </h1>

              {story.summary && (
                <p className="mt-2.5 max-w-2xl text-sm leading-relaxed text-muted-foreground sm:text-base">
                  {story.summary}
                </p>
              )}

              <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <span className="inline-flex items-center gap-1">
                  <Users className="h-3.5 w-3.5" aria-hidden />
                  {t("stories.publishers", { n: publisherCount })}
                </span>
                <span className="inline-flex items-center gap-1">
                  <Newspaper className="h-3.5 w-3.5" aria-hidden />
                  {t("stories.articlesCount", { n: story.totalCoverage })}
                </span>
                {story.earliest && <span>{t("stories.firstReport", { date: fmtDate(story.earliest) })}</span>}
                {story.latest && story.latest !== story.earliest && (
                  <span>{t("stories.latestReport", { date: fmtDate(story.latest) })}</span>
                )}
                {story.updatedAt && <span>{timeAgo(story.updatedAt)}</span>}
              </div>

              <div className="mt-4 max-w-md">
                <SpectrumBar distribution={story.distribution} height={10} />
                {story.blindspotSide && (
                  <p
                    className="mt-2 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[0.68rem] font-medium"
                    style={{
                      color: LEAN_META[story.blindspotSide].color,
                      borderColor: LEAN_META[story.blindspotSide].color,
                    }}
                  >
                    <EyeOff className="h-3 w-3" aria-hidden />
                    {t("stories.thinCoverage", { side: t(`filter.${story.blindspotSide}`).toLowerCase() })}
                  </p>
                )}
              </div>
            </div>
          </article>

          {/* How is it covered — every article, filterable by the facets the data really has. */}
          <CoverageList coverage={story.coverage} />

          {/* How it developed — freshness / momentum / timeline (existing panel, unchanged). */}
          <StoryIntelligencePanel storyId={story.id} />

          {/* What to read next — same-topic first, from the same cached top-stories page. */}
          {related.length > 0 && (
            <section aria-labelledby="related-heading">
              <SectionHeader
                id="related-heading"
                title={t("story.related")}
                href="/stories"
                actionLabel={t("home.viewAll")}
              />
              <ul className="divide-y">
                {related.map((s) => (
                  <StoryListItem key={s.id} story={s} variant="compact" showImage />
                ))}
              </ul>
            </section>
          )}
      </PageGrid>
    </PageContainer>
  );
}
