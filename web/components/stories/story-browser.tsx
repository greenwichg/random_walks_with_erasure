"use client";

import * as React from "react";
import type { LucideIcon } from "lucide-react";
import { useStories, useDiscover } from "@/hooks/use-data";
import type { StoryQuery } from "@/types/domain";
import { PageContainer } from "@/components/layout/page-container";
import { StoryCard } from "@/components/stories/story-card";
import { FilterSelect, type FilterOption } from "@/components/shared/filter-select";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";

const LEAN_OPTIONS: FilterOption[] = [
  { value: "left", label: "Left" },
  { value: "center", label: "Center" },
  { value: "right", label: "Right" },
];
const SORT_OPTIONS: FilterOption[] = [
  { value: "top", label: "Top" },
  { value: "latest", label: "Latest" },
  { value: "publishers", label: "Most sources" },
];
const opt = (values: string[]): FilterOption[] => values.map((v) => ({ value: v, label: v }));
const asFilter = (v: string) => (v === "all" ? undefined : v);
const PAGE = 24;

/**
 * The shared Story browsing surface — filters (topic / publisher / lean), sort, and pagination over
 * the single Story Service (`/api/stories`). Both Discover and Stories render this; they differ only
 * in copy and default sort. Facet values come from the catalog (`/api/discover`). Opening a Story
 * (its card) goes to the detail page, where each article opens its canonical publisher URL.
 */
export function StoryBrowser({
  title,
  description,
  icon,
  defaultSort = "top",
  emptyDescription,
}: {
  title: string;
  description: string;
  icon: LucideIcon;
  defaultSort?: string;
  emptyDescription: string;
}) {
  const [topic, setTopic] = React.useState("all");
  const [publisher, setPublisher] = React.useState("all");
  const [lean, setLean] = React.useState("all");
  const [sort, setSort] = React.useState(defaultSort);
  const [offset, setOffset] = React.useState(0);

  React.useEffect(() => {
    setOffset(0);
  }, [topic, publisher, lean, sort]);

  const facets = useDiscover({});
  const { data, isLoading, isError, refetch, isFetching } = useStories({
    topic: asFilter(topic),
    publisher: asFilter(publisher),
    lean: asFilter(lean),
    sort: sort as StoryQuery["sort"],
    limit: PAGE,
    offset,
  });

  const stories = data?.stories ?? [];
  const total = data?.total ?? 0;
  const page = data?.page ?? 1;
  const hasMore = data?.hasMore ?? false;

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">{description}</p>
      </div>

      <div className="mb-6 flex flex-wrap items-center gap-2">
        <FilterSelect label="Topic" value={topic} options={opt(facets.data?.topics ?? [])} onChange={setTopic} />
        <FilterSelect
          label="Publisher"
          value={publisher}
          options={opt(facets.data?.publishers ?? [])}
          onChange={setPublisher}
        />
        <FilterSelect label="Lean" value={lean} options={LEAN_OPTIONS} onChange={setLean} />
        <FilterSelect label="Sort" value={sort} options={SORT_OPTIONS} onChange={setSort} allLabel="Top" />
        {total > 0 && (
          <span className="ml-auto text-sm text-muted-foreground">
            {total} {total === 1 ? "story" : "stories"}
          </span>
        )}
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
        <EmptyState icon={icon} title="No stories yet" description={emptyDescription} className="mt-4" />
      )}

      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        {stories.map((story, i) => (
          <StoryCard key={story.id} story={story} index={i} />
        ))}
      </div>

      {(page > 1 || hasMore) && (
        <div className="mt-8 flex items-center justify-center gap-3">
          <button
            disabled={offset === 0 || isFetching}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
            className="inline-flex h-9 items-center rounded-lg border bg-card px-4 text-sm font-medium transition-colors hover:bg-accent disabled:opacity-50"
          >
            Previous
          </button>
          <span className="text-sm text-muted-foreground">Page {page}</span>
          <button
            disabled={!hasMore || isFetching}
            onClick={() => setOffset(offset + PAGE)}
            className="inline-flex h-9 items-center rounded-lg border bg-card px-4 text-sm font-medium transition-colors hover:bg-accent disabled:opacity-50"
          >
            Next
          </button>
        </div>
      )}
    </PageContainer>
  );
}
