"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { Search as SearchIcon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useSearch, useDiscover } from "@/hooks/use-data";
import { services, queryKeys } from "@/services";
import { useTranslation } from "@/lib/i18n";
import type { SearchParams } from "@/types/domain";
import { PageContainer } from "@/components/layout/page-container";
import { DiscoverCard } from "@/components/discover/discover-card";
import { FilterSelect, type FilterOption } from "@/components/shared/filter-select";
import { CountryBadge } from "@/components/shared/country-badge";
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
  const { t } = useTranslation();
  const [q, setQ] = React.useState(initial);
  const [topic, setTopic] = React.useState("all");
  const [publisher, setPublisher] = React.useState("all");
  const [lean, setLean] = React.useState("all");
  const [country, setCountry] = React.useState("all");
  const [sort, setSort] = React.useState("newest");
  const [offset, setOffset] = React.useState(0);

  // Any new query or filter resets to the first page.
  React.useEffect(() => {
    setOffset(0);
  }, [q, topic, publisher, lean, country, sort]);

  const facets = useDiscover({}); // whole-catalog facet values for the filter dropdowns
  // Located-catalog countries — the filter only ever offers a country with data behind it.
  const countries = useQuery({ queryKey: queryKeys.placeCountries, queryFn: services.placeCountries });
  const countryOptions = React.useMemo(
    () =>
      (countries.data ?? [])
        .filter((c) => c.articles > 0)
        .map((c) => ({ value: c.country, label: <CountryBadge code={c.country} /> })),
    [countries.data],
  );
  const { data, isLoading, isError, refetch, isFetching } = useSearch({
    query: q.trim() || undefined,
    topic: asFilter(topic),
    publisher: asFilter(publisher),
    lean: asFilter(lean),
    country: asFilter(country),
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
        <h1 className="text-2xl font-semibold tracking-tight">{t("search.title")}</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">{t("search.subtitle")}</p>
      </div>

      <div className="mb-4 flex items-center gap-2 rounded-lg border bg-card px-3">
        <SearchIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t("search.placeholder")}
          className="h-11 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
        />
      </div>

      <div className="mb-6 flex flex-wrap items-center gap-2">
        <FilterSelect label={t("filter.topic")} value={topic} options={opt(facets.data?.topics ?? [])} onChange={setTopic} />
        <FilterSelect
          label={t("filter.publisher")}
          value={publisher}
          options={opt(facets.data?.publishers ?? [])}
          onChange={setPublisher}
        />
        <FilterSelect label={t("filter.lean")} value={lean} options={LEAN_OPTIONS} onChange={setLean} />
        {countryOptions.length > 0 && (
          <FilterSelect label={t("filter.country")} value={country} options={countryOptions} onChange={setCountry} />
        )}
        <FilterSelect label={t("filter.sort")} value={sort} options={SORT_OPTIONS} onChange={setSort} resettable={false} />
        {total > 0 && (
          <span className="ml-auto text-sm text-muted-foreground">
            {total === 1 ? t("common.resultOne", { n: total }) : t("common.results", { n: total })}
          </span>
        )}
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-56 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && results.length === 0 && (
        <EmptyState
          icon={SearchIcon}
          title={t("search.empty.title")}
          description={t("search.empty.body")}
          className="mt-4"
        />
      )}

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
        {results.map((article, i) => (
          <DiscoverCard key={article.id} article={article} index={i} openedFrom="search" />
        ))}
      </div>

      {(page > 1 || hasMore) && (
        <div className="mt-8 flex items-center justify-center gap-3">
          <button
            disabled={offset === 0 || isFetching}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
            className="inline-flex h-9 items-center rounded-lg border bg-card px-4 text-sm font-medium transition-colors hover:bg-accent disabled:opacity-50"
          >
            {t("common.previous")}
          </button>
          <span className="text-sm text-muted-foreground">{t("common.page", { n: page })}</span>
          <button
            disabled={!hasMore || isFetching}
            onClick={() => setOffset(offset + PAGE)}
            className="inline-flex h-9 items-center rounded-lg border bg-card px-4 text-sm font-medium transition-colors hover:bg-accent disabled:opacity-50"
          >
            {t("common.next")}
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
