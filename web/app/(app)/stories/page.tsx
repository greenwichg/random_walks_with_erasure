"use client";

import * as React from "react";
import { Search, Layers } from "lucide-react";
import { useStories } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { StoryCard } from "@/components/stories/story-card";
import { TopicChip } from "@/components/shared/topic-chip";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

export default function StoriesPage() {
  const { data, isLoading, isError, refetch } = useStories();
  const [q, setQ] = React.useState("");
  const [topic, setTopic] = React.useState<string | null>(null);

  const topics = React.useMemo(
    () => [...new Set((data ?? []).map((s) => s.topic))].sort(),
    [data],
  );

  const filtered = (data ?? []).filter((s) => {
    if (topic && s.topic !== topic) return false;
    if (q && !`${s.title} ${s.summary} ${s.topic}`.toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  });

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Stories</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">
          One event, every viewpoint. We cluster coverage across publishers so you can see how the
          same story is framed — left, center, and right — side by side.
        </p>
      </div>

      {/* controls */}
      <div className="mb-6 space-y-3">
        <div className="relative max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search stories…" className="pl-9" />
        </div>
        {topics.length > 0 && (
          <div className="flex flex-wrap gap-2">
            <TopicChip topic="All topics" active={topic === null} onClick={() => setTopic(null)} />
            {topics.map((t) => (
              <TopicChip key={t} topic={t} active={topic === t} onClick={() => setTopic(topic === t ? null : t)} />
            ))}
          </div>
        )}
      </div>

      {isLoading && (
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-56 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && filtered.length === 0 && (
        <EmptyState icon={Layers} title="No stories found" description="Try a different topic or search term." />
      )}

      {data && filtered.length > 0 && (
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {filtered.map((s, i) => (
            <StoryCard key={s.id} story={s} index={i} />
          ))}
        </div>
      )}
    </PageContainer>
  );
}
