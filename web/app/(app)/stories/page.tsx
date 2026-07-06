"use client";

import { Newspaper } from "lucide-react";
import { useStories } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { StoryCard } from "@/components/stories/story-card";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";

export default function StoriesPage() {
  const { data, isLoading, isError, refetch } = useStories();
  const stories = data ?? [];

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Stories</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">
          One event, every viewpoint — the same story clustered across left, center, and right publishers.
        </p>
      </div>

      {isLoading && (
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-52 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && stories.length === 0 && (
        <EmptyState
          icon={Newspaper}
          title="No stories yet"
          description="Stories cluster the live news catalog into events. Once enough articles across publishers cover the same event, they appear here."
          className="mt-4"
        />
      )}

      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        {stories.map((story, i) => (
          <StoryCard key={story.id} story={story} index={i} />
        ))}
      </div>
    </PageContainer>
  );
}
