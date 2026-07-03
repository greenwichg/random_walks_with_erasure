"use client";

import * as React from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { TrendingUp, Compass, ArrowRight, Newspaper, Sparkles } from "lucide-react";
import type { Story } from "@/types/domain";
import { useStories, useTopics } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { StoryCard } from "@/components/stories/story-card";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { TopicChip } from "@/components/shared/topic-chip";
import { ErrorState, EmptyState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { compact } from "@/lib/utils";

export default function DiscoverPage() {
  const { data: stories, isLoading, isError, refetch } = useStories();
  const { data: topics } = useTopics();
  const [topic, setTopic] = React.useState<string | null>(null);

  const ranked = React.useMemo(
    () => [...(stories ?? [])].sort((a, b) => b.totalCoverage - a.totalCoverage),
    [stories],
  );
  const featured = ranked[0];
  const rest = ranked
    .slice(1)
    .filter((s) => (topic ? s.topic === topic : true));

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Discover</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">
          What's driving the news right now — clustered across every publisher, so you can read the
          whole picture instead of one side of it.
        </p>
      </div>

      {isLoading && (
        <div className="space-y-6">
          <Skeleton className="h-64 rounded-lg" />
          <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-56 rounded-lg" />
            ))}
          </div>
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {featured && (
        <>
          <FeaturedStory story={featured} />

          {/* Explore by topic */}
          {topics && topics.length > 0 && (
            <section className="mt-8">
              <div className="mb-3 flex items-center gap-2">
                <Compass className="h-4 w-4 text-muted-foreground" />
                <h2 className="text-sm font-semibold">Explore by topic</h2>
              </div>
              <div className="flex flex-wrap gap-2">
                <TopicChip topic="All" active={topic === null} onClick={() => setTopic(null)} />
                {topics.map((t) => (
                  <TopicChip
                    key={t.topic}
                    topic={t.topic}
                    share={t.share}
                    active={topic === t.topic}
                    onClick={() => setTopic(topic === t.topic ? null : t.topic)}
                  />
                ))}
              </div>
            </section>
          )}

          {/* Trending */}
          <section className="mt-8">
            <div className="mb-4 flex items-center gap-2">
              <TrendingUp className="h-5 w-5 text-primary" />
              <h2 className="text-lg font-semibold tracking-tight">Trending now</h2>
            </div>
            {rest.length === 0 ? (
              <EmptyState
                icon={Sparkles}
                title="Nothing else under this topic"
                description="Clear the topic filter to see everything that's trending."
              />
            ) : (
              <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
                {rest.map((s, i) => (
                  <StoryCard key={s.id} story={s} index={i} />
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </PageContainer>
  );
}

/** The lead story — a full-width hero with its coverage spectrum. */
function FeaturedStory({ story }: { story: Story }) {
  const publishers = [...new Set(story.coverage.map((c) => c.publisher))];
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ease: [0.16, 1, 0.3, 1] }}
      className="overflow-hidden rounded-xl border bg-gradient-to-br from-primary/[0.07] via-card to-card shadow-soft"
    >
      <div className="grid gap-6 p-6 lg:grid-cols-[1.5fr_1fr] lg:p-8">
        <div className="flex flex-col">
          <div className="mb-3 flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-primary px-2.5 py-0.5 text-xs font-medium text-primary-foreground">
              <TrendingUp className="h-3 w-3" /> Top story
            </span>
            <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
              {story.topic}
            </span>
          </div>
          <h2 className="text-xl font-semibold leading-tight tracking-tight sm:text-2xl">{story.title}</h2>
          <p className="mt-2 max-w-prose text-sm text-muted-foreground">{story.summary}</p>
          <div className="mt-4 flex items-center gap-4 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <Newspaper className="h-3.5 w-3.5" /> {compact(story.totalCoverage)} sources
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="font-medium text-foreground/70">{publishers.slice(0, 3).join(", ")}</span>
              {publishers.length > 3 && <span>+{publishers.length - 3} more</span>}
            </span>
          </div>
          <div className="mt-auto pt-5">
            <Button asChild>
              <Link href={`/stories/${story.id}`}>
                See all coverage <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
          </div>
        </div>

        <div className="flex flex-col justify-center gap-3 rounded-lg border bg-card/60 p-5">
          <p className="text-xs font-medium text-muted-foreground">How the coverage splits</p>
          <SpectrumBar distribution={story.distribution} height={12} />
          {story.blindspotSide && (
            <p className="mt-1 text-xs text-muted-foreground">
              Coverage is thin on the{" "}
              <span className="font-medium text-foreground">{story.blindspotSide}</span> — worth
              seeking out to get the full picture.
            </p>
          )}
        </div>
      </div>
    </motion.div>
  );
}
