"use client";

import * as React from "react";
import { Compass } from "lucide-react";
import { useDiscover } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { DiscoverCard } from "@/components/discover/discover-card";
import { FilterSelect, type FilterOption } from "@/components/shared/filter-select";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";

const LEAN_OPTIONS: FilterOption[] = [
  { value: "left", label: "Left" },
  { value: "center", label: "Center" },
  { value: "right", label: "Right" },
];
const opt = (values: string[]): FilterOption[] => values.map((v) => ({ value: v, label: v }));
const asFilter = (v: string) => (v === "all" ? undefined : v);

export default function DiscoverPage() {
  const [topic, setTopic] = React.useState("all");
  const [publisher, setPublisher] = React.useState("all");
  const [lean, setLean] = React.useState("all");

  const { data, isLoading, isError, refetch } = useDiscover({
    topic: asFilter(topic),
    publisher: asFilter(publisher),
    lean: asFilter(lean),
  });

  const articles = data?.articles ?? [];

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Discover</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">
          The latest across every publisher — filter by topic, source, or lean, and open the real article.
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
        <FilterSelect label="Lean" value={lean} options={LEAN_OPTIONS} onChange={setLean} allLabel="Latest" />
      </div>

      {isLoading && (
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-56 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && articles.length === 0 && (
        <EmptyState
          icon={Compass}
          title="No articles yet"
          description="Discover reads the live news catalog. Once RSS ingestion has run (RWE_RECS_SOURCE=feed), the latest articles across publishers appear here."
          className="mt-4"
        />
      )}

      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        {articles.map((article, i) => (
          <DiscoverCard key={article.id} article={article} index={i} />
        ))}
      </div>
    </PageContainer>
  );
}
