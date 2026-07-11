"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Newspaper, Users, EyeOff } from "lucide-react";
import { useStory } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { LeanBadge } from "@/components/shared/article-badges";
import { ArticleImage } from "@/components/shared/article-image";
import { StoryIntelligencePanel } from "@/components/stories/story-intelligence-panel";
import { ReadArticleButton } from "@/components/shared/read-article-button";
import { SaveButton } from "@/components/shared/save-button";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";
import { LEAN_META } from "@/lib/metrics";
import { useTranslation } from "@/lib/i18n";
import { activeLang, formatDate } from "@/lib/i18n-core";

const fmtDate = (iso?: string) =>
  iso ? formatDate(iso, activeLang(), { month: "short", day: "numeric", year: "numeric" }) : "";

export default function StoryDetailPage() {
  const { t, timeAgo } = useTranslation();
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const { data: story, isLoading, isError, refetch } = useStory(id);

  const back = (
    <Link
      href="/stories"
      className="mb-5 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
    >
      <ArrowLeft className="h-4 w-4" /> {t("stories.back")}
    </Link>
  );

  if (isLoading) {
    return (
      <PageContainer>
        {back}
        <Skeleton className="h-8 w-2/3 rounded" />
        <Skeleton className="mt-4 h-24 rounded-lg" />
        <div className="mt-4 space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-lg" />
          ))}
        </div>
      </PageContainer>
    );
  }
  if (isError) {
    return (
      <PageContainer>
        {back}
        <ErrorState onRetry={() => refetch()} />
      </PageContainer>
    );
  }
  if (!story) {
    return (
      <PageContainer>
        {back}
        <EmptyState
          icon={Newspaper}
          title={t("stories.notFound.title")}
          description={t("stories.notFound.body")}
        />
      </PageContainer>
    );
  }

  const publisherCount = story.publisherCount ?? new Set(story.coverage.map((c) => c.publisher)).size;

  return (
    <PageContainer>
      {back}

      <div className="rounded-lg border bg-card p-6 shadow-soft">
        <ArticleImage src={story.image} alt={story.title} className="mb-4" aspect="aspect-[21/9]" />
        <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
          {story.topic}
        </span>
        <h1 className="mt-3 text-2xl font-semibold leading-tight tracking-tight">{story.title}</h1>
        {story.summary && <p className="mt-2 max-w-2xl text-sm text-muted-foreground">{story.summary}</p>}

        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <Users className="h-3.5 w-3.5" /> {t("stories.publishers", { n: publisherCount })}
          </span>
          <span className="inline-flex items-center gap-1">
            <Newspaper className="h-3.5 w-3.5" /> {t("stories.articlesCount", { n: story.totalCoverage })}
          </span>
          {story.earliest && <span>{t("stories.firstReport", { date: fmtDate(story.earliest) })}</span>}
          {story.latest && story.latest !== story.earliest && <span>{t("stories.latestReport", { date: fmtDate(story.latest) })}</span>}
        </div>

        <div className="mt-4 max-w-md">
          <SpectrumBar distribution={story.distribution} height={10} showLegend />
          {story.blindspotSide && (
            <p
              className="mt-2 inline-flex items-center gap-1 text-xs font-medium"
              style={{ color: LEAN_META[story.blindspotSide].color }}
            >
              <EyeOff className="h-3.5 w-3.5" />
              {t("stories.thinCoverage", { side: t(`filter.${story.blindspotSide}`).toLowerCase() })}
            </p>
          )}
        </div>
      </div>

      <StoryIntelligencePanel storyId={story.id} />

      <h2 className="mb-3 mt-6 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        {t("stories.coverageAcross")}
      </h2>
      <div className="space-y-3">
        {story.coverage.map((c, i) => (
          <div
            key={`${c.publisher}-${i}`}
            className="flex flex-col gap-3 rounded-lg border bg-card p-4 shadow-soft sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">{c.publisher}</span>
                <span>·</span>
                <LeanBadge lean={c.lean} bucket={c.leanBucket} />
                <span>·</span>
                <span>{timeAgo(c.publishedAt)}</span>
              </div>
              <h3 className="mt-1 font-medium leading-snug">{c.headline}</h3>
            </div>
            <div className="flex shrink-0 items-center gap-2 self-start sm:self-center">
              <ReadArticleButton article={{ url: c.url, headline: c.headline }} openedFrom="stories" />
              {c.url && (
                <SaveButton
                  article={{ id: c.url, url: c.url, headline: c.headline, publisher: c.publisher,
                             publishedAt: c.publishedAt }}
                />
              )}
            </div>
          </div>
        ))}
      </div>
    </PageContainer>
  );
}
