"use client";

import * as React from "react";
import { Compass } from "lucide-react";
import { useDiscover } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { DiscoverCard } from "@/components/discover/discover-card";
import { FilterSelect, type FilterOption } from "@/components/shared/filter-select";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";

// Discover is the ARTICLE-centric surface: the latest individual FeedArticles from the live catalog
// (/api/discover), newest first, with topic/publisher/lean filters. Stories is the event-centric
// surface (clustered coverage) — the two are deliberately distinct. Each card opens the real
// publisher URL via the shared Read flow. No Story Service / clustering involved here.

const LEAN_OPTIONS: FilterOption[] = [
  { value: "left", label: "Left" },
  { value: "center", label: "Center" },
  { value: "right", label: "Right" },
];
const opt = (values: string[]): FilterOption[] => values.map((v) => ({ value: v, label: v }));
const asFilter = (v: string) => (v === "all" ? undefined : v);
const PAGE = 24;
// /api/discover returns a flat, size-capped list (no offset); fetch the cap once and page on the
// client. The live catalog sits well within this, and it never exceeds what the endpoint would serve.
const FETCH = 200;

export default function DiscoverPage() {
  const [topic, setTopic] = React.useState("all");
  const [publisher, setPublisher] = React.useState("all");
  const [lean, setLean] = React.useState("all");
  const [offset, setOffset] = React.useState(0);

  // Any filter change resets to the first page.
  React.useEffect(() => {
    setOffset(0);
  }, [topic, publisher, lean]);

  const { data, isLoading, isError, refetch } = useDiscover({
    topic: asFilter(topic),
    publisher: asFilter(publisher),
    lean: asFilter(lean),
    limit: FETCH,
  });

  const articles = data?.articles ?? [];
  const total = articles.length;
  const paged = articles.slice(offset, offset + PAGE);
  const page = Math.floor(offset / PAGE) + 1;
  const hasMore = offset + PAGE < total;

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Discover</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">
          The latest articles across every publisher — newest first. Filter by topic, publisher, or
          lean, and open the real article. Looking for how one event is covered across the spectrum? Try
          Stories.
        </p>
      </div>

      <div className="mb-6 flex flex-wrap items-center gap-2">
        <FilterSelect label="Topic" value={topic} options={opt(data?.topics ?? [])} onChange={setTopic} />
        <FilterSelect
          label="Publisher"
          value={publisher}
          options={opt(data?.publishers ?? [])}
          onChange={setPublisher}
        />
        <FilterSelect label="Lean" value={lean} options={LEAN_OPTIONS} onChange={setLean} />
        {total > 0 && (
          <span className="ml-auto text-sm text-muted-foreground">
            {total} article{total === 1 ? "" : "s"}
          </span>
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

      {data && total === 0 && (
        <EmptyState
          icon={Compass}
          title="No articles yet"
          description="Discover shows the latest articles from the live news catalog. Once RSS ingestion has run (RWE_RECS_SOURCE=feed), fresh articles appear here."
          className="mt-4"
        />
      )}

      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        {paged.map((article, i) => (
          <DiscoverCard key={article.id} article={article} index={i} />
        ))}
      </div>

      {(page > 1 || hasMore) && (
        <div className="mt-8 flex items-center justify-center gap-3">
          <button
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
            className="inline-flex h-9 items-center rounded-lg border bg-card px-4 text-sm font-medium transition-colors hover:bg-accent disabled:opacity-50"
          >
            Previous
          </button>
          <span className="text-sm text-muted-foreground">Page {page}</span>
          <button
            disabled={!hasMore}
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
