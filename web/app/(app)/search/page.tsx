"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { Search as SearchIcon } from "lucide-react";
import { useSearch, useDiscover } from "@/hooks/use-data";
import type { SearchParams } from "@/types/domain";
import { PageContainer } from "@/components/layout/page-container";
import { DiscoverCard } from "@/components/discover/discover-card";
import { FilterSelect, type FilterOption } from "@/components/shared/filter-select";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

const LEAN_OPTIONS: FilterOption[] = [
  { value: "left", label: "Left" },
  { value: "center", label: "Center" },
  { value: "right", label: "Right" },
];
const SORT_OPTIONS: FilterOption[] = [
  { value: "newest", label: "Newest" },
  { value: "oldest", label: "Oldest" },
  { value: "publisher", label: "Publisher" },
];
const opt = (values: string[]): FilterOption[] => values.map((v) => ({ value: v, label: v }));
const asFilter = (v: string) => (v === "all" ? undefined : v);
const PAGE = 24;

function SearchInner() {
  const initial = useSearchParams().get("query") ?? "";
  const [q, setQ] = React.useState(initial);
  const [topic, setTopic] = React.useState("all");
  const [publisher, setPublisher] = React.useState("all");
  const [lean, setLean] = React.useState("all");
  const [sort, setSort] = React.useState("newest");
  const [offset, setOffset] = React.useState(0);

  // Any new query or filter resets to the first page.
  React.useEffect(() => {
    setOffset(0);
  }, [q, topic, publisher, lean, sort]);

  const facets = useDiscover({}); // whole-catalog facet values for the filter dropdowns
  const { data, isLoading, isError, refetch, isFetching } = useSearch({
    query: q.trim() || undefined,
    topic: asFilter(topic),
    publisher: asFilter(publisher),
    lean: asFilter(lean),
    sort: sort as SearchParams["sort"],
    limit: PAGE,
    offset,
  });

  const results = data?.results ?? [];
  const total = data?.total ?? 0;
  const page = data?.page ?? 1;
  const hasMore = data?.hasMore ?? false;

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Search</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">
          Search the live news catalog across every publisher — filter, sort, and open the real article.
        </p>
      </div>

      <div className="mb-4 flex items-center gap-2 rounded-lg border bg-card px-3">
        <SearchIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search articles, publishers, topics…"
          className="h-11 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
        />
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
        <FilterSelect label="Sort" value={sort} options={SORT_OPTIONS} onChange={setSort} allLabel="Newest" />
        {total > 0 && (
          <span className="ml-auto text-sm text-muted-foreground">
            {total} result{total === 1 ? "" : "s"}
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

      {data && results.length === 0 && (
        <EmptyState
          icon={SearchIcon}
          title="No matching articles"
          description="Try a different query or clear the filters. Search reads the live news catalog, populated once RSS ingestion has run (RWE_RECS_SOURCE=feed)."
          className="mt-4"
        />
      )}

      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        {results.map((article, i) => (
          <DiscoverCard key={article.id} article={article} index={i} />
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

export default function SearchPage() {
  return (
    <React.Suspense fallback={<PageContainer>{null}</PageContainer>}>
      <SearchInner />
    </React.Suspense>
  );
}
