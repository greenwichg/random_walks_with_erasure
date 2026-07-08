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
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";
import { LEAN_META } from "@/lib/metrics";
import { timeAgo } from "@/lib/utils";

const fmtDate = (iso?: string) => {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
};

export default function StoryDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const { data: story, isLoading, isError, refetch } = useStory(id);

  const back = (
    <Link
      href="/stories"
      className="mb-5 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
    >
      <ArrowLeft className="h-4 w-4" /> All stories
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
          title="Story not found"
          description="This event is no longer in the live catalog, or its coverage has changed. Head back to Stories for the latest events."
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
            <Users className="h-3.5 w-3.5" /> {publisherCount} publishers
          </span>
          <span className="inline-flex items-center gap-1">
            <Newspaper className="h-3.5 w-3.5" /> {story.totalCoverage} articles
          </span>
          {story.earliest && <span>First report {fmtDate(story.earliest)}</span>}
          {story.latest && story.latest !== story.earliest && <span>Latest {fmtDate(story.latest)}</span>}
        </div>

        <div className="mt-4 max-w-md">
          <SpectrumBar distribution={story.distribution} height={10} showLegend />
          {story.blindspotSide && (
            <p
              className="mt-2 inline-flex items-center gap-1 text-xs font-medium"
              style={{ color: LEAN_META[story.blindspotSide].color }}
            >
              <EyeOff className="h-3.5 w-3.5" />
              Thin coverage on the {LEAN_META[story.blindspotSide].label.toLowerCase()}
            </p>
          )}
        </div>
      </div>

      <StoryIntelligencePanel storyId={story.id} />

      <h2 className="mb-3 mt-6 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        Coverage across publishers
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
            <ReadArticleButton article={{ url: c.url }} className="shrink-0 self-start sm:self-center" />
          </div>
        ))}
      </div>
    </PageContainer>
  );
}
