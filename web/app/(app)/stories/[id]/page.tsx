"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { motion } from "framer-motion";
import { ArrowLeft, Sparkles, Newspaper, Clock, EyeOff, FileText } from "lucide-react";
import type { LeanBucket, Story, StoryCoverage } from "@/types/domain";
import { useStory } from "@/hooks/use-data";
import { leanBucket } from "@/lib/political";
import { LEAN_META } from "@/lib/metrics";
import { PageContainer } from "@/components/layout/page-container";
import { SectionCard } from "@/components/shared/section-card";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { LeanBadge, RegisterBadge, EmotionBadge } from "@/components/shared/article-badges";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";
import { compact, timeAgo, cn } from "@/lib/utils";

const BUCKETS: LeanBucket[] = ["left", "center", "right"];

export default function StoryDetailPage() {
  const params = useParams<{ id: string }>();
  const { data: story, isLoading, isError, refetch } = useStory(params.id);

  return (
    <PageContainer>
      <Link
        href="/stories"
        className="mb-5 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> All stories
      </Link>

      {isLoading && <StorySkeleton />}
      {isError && <ErrorState onRetry={() => refetch()} />}
      {!isLoading && !isError && !story && (
        <EmptyState icon={Newspaper} title="Story not found" description="This story may have been removed." />
      )}

      {story && <StoryDetail story={story} />}
    </PageContainer>
  );
}

function StoryDetail({ story }: { story: Story }) {
  const total = story.coverage.length || 1;
  const reportingShare = story.coverage.filter((c) => c.register === "reporting").length / total;
  const byBucket = (b: LeanBucket) =>
    story.coverage
      .filter((c) => leanBucket(c.lean) === b)
      .sort((x, y) => x.lean - y.lean);

  return (
    <>
      {/* Header */}
      <div className="mb-6">
        <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
          <span className="rounded-full bg-primary/10 px-2.5 py-0.5 font-medium text-primary">{story.topic}</span>
          <span className="inline-flex items-center gap-1 text-muted-foreground">
            <Newspaper className="h-3.5 w-3.5" /> {compact(story.totalCoverage)} sources
          </span>
          <span className="inline-flex items-center gap-1 text-muted-foreground">
            <Clock className="h-3.5 w-3.5" /> Updated {timeAgo(story.updatedAt)}
          </span>
        </div>
        <h1 className="max-w-3xl text-2xl font-semibold leading-tight tracking-tight sm:text-3xl">
          {story.title}
        </h1>
      </div>

      {/* AI summary */}
      <div className="mb-6 rounded-lg border bg-gradient-to-br from-primary/[0.06] to-card p-5">
        <div className="mb-2 flex items-center gap-2">
          <span className="grid h-6 w-6 place-items-center rounded-md bg-primary/12 text-primary">
            <Sparkles className="h-3.5 w-3.5" />
          </span>
          <h2 className="text-sm font-semibold">AI summary</h2>
          <span className="rounded-full bg-muted px-2 py-0.5 text-[0.65rem] font-medium text-muted-foreground">
            Neutral synthesis
          </span>
        </div>
        <p className="max-w-prose text-sm leading-relaxed text-foreground/90">{story.summary}</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        {/* Coverage across the spectrum */}
        <div>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold tracking-tight">Coverage across the spectrum</h2>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            {BUCKETS.map((b) => (
              <CoverageColumn key={b} bucket={b} items={byBucket(b)} />
            ))}
          </div>
        </div>

        {/* Sidebar */}
        <aside className="space-y-6">
          <SectionCard title="Distribution" info="How this story's coverage splits across the spectrum.">
            <SpectrumBar distribution={story.distribution} height={12} />
            {story.blindspotSide && (
              <div
                className="mt-4 flex items-start gap-2 rounded-lg border p-3 text-xs"
                style={{ borderColor: `${LEAN_META[story.blindspotSide].color}40` }}
              >
                <EyeOff
                  className="mt-0.5 h-4 w-4 shrink-0"
                  style={{ color: LEAN_META[story.blindspotSide].color }}
                />
                <p className="text-muted-foreground">
                  Coverage is thin on the{" "}
                  <span className="font-medium text-foreground">
                    {LEAN_META[story.blindspotSide].label.toLowerCase()}
                  </span>
                  . Seek out that side to avoid a one-sided read.
                </p>
              </div>
            )}
          </SectionCard>

          <SectionCard title="Tone of coverage" info="The mix of reporting vs. opinion in this cluster.">
            <div className="space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                  <FileText className="h-3.5 w-3.5" /> Reporting
                </span>
                <span className="font-medium tabular-nums">{Math.round(reportingShare * 100)}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full bg-positive"
                  style={{ width: `${Math.round(reportingShare * 100)}%` }}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                {Math.round(reportingShare * 100)}% of outlets frame this as straight reporting; the rest
                is opinion or analysis.
              </p>
            </div>
          </SectionCard>

          <SectionCard title="Timeline" info="How the story developed.">
            <ol className="relative space-y-5 border-l border-border pl-5">
              {story.timeline.map((t, i) => (
                <motion.li
                  key={i}
                  initial={{ opacity: 0, x: -6 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: Math.min(i * 0.06, 0.3) }}
                  className="relative"
                >
                  <span className="absolute -left-[1.55rem] top-0.5 h-2.5 w-2.5 rounded-full border-2 border-background bg-primary" />
                  <p className="text-sm font-medium leading-tight">{t.label}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">{timeAgo(t.date)}</p>
                </motion.li>
              ))}
            </ol>
          </SectionCard>
        </aside>
      </div>
    </>
  );
}

/** One side of the spectrum: a labeled column of publisher coverage. */
function CoverageColumn({ bucket, items }: { bucket: LeanBucket; items: StoryCoverage[] }) {
  const meta = LEAN_META[bucket];
  return (
    <div className="rounded-lg border bg-card/40">
      <div
        className="flex items-center justify-between rounded-t-lg border-b px-3 py-2"
        style={{ borderTopColor: meta.color, borderTopWidth: 3 }}
      >
        <span className="text-sm font-semibold" style={{ color: meta.color }}>
          {meta.label}
        </span>
        <span className="text-xs text-muted-foreground">{items.length}</span>
      </div>
      <div className="space-y-2 p-2">
        {items.length === 0 ? (
          <p className="px-2 py-6 text-center text-xs text-muted-foreground">
            No coverage from the {meta.label.toLowerCase()}.
          </p>
        ) : (
          items.map((c, i) => <CoverageItem key={`${c.publisher}-${i}`} item={c} index={i} />)
        )}
      </div>
    </div>
  );
}

function CoverageItem({ item, index }: { item: StoryCoverage; index: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index * 0.04, 0.25) }}
      className={cn(
        "rounded-md border bg-card p-3 transition-colors hover:bg-accent/40",
        item.url && "cursor-pointer",
      )}
    >
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="truncate text-xs font-semibold">{item.publisher}</span>
        <span className="shrink-0 text-[0.65rem] text-muted-foreground">{timeAgo(item.publishedAt)}</span>
      </div>
      <p className="mb-2 line-clamp-3 text-sm leading-snug">{item.headline}</p>
      <div className="flex flex-wrap items-center gap-1.5">
        <LeanBadge lean={item.lean} />
        <RegisterBadge register={item.register} />
        <EmotionBadge emotion={item.emotion} />
      </div>
    </motion.div>
  );
}

function StorySkeleton() {
  return (
    <div>
      <Skeleton className="mb-3 h-5 w-40" />
      <Skeleton className="mb-6 h-9 w-2/3" />
      <Skeleton className="mb-6 h-24 rounded-lg" />
      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <div className="grid gap-4 md:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-64 rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-64 rounded-lg" />
      </div>
    </div>
  );
}
