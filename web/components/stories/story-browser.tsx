"use client";

import * as React from "react";
import type { LucideIcon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useStories, useDiscover } from "@/hooks/use-data";
import { services, queryKeys } from "@/services";
import { useTranslation } from "@/lib/i18n";
import type { StoryQuery } from "@/types/domain";
import { PageContainer } from "@/components/layout/page-container";
import { StoryCard } from "@/components/stories/story-card";
import { FilterSelect, type FilterOption } from "@/components/shared/filter-select";
import { CountryBadge } from "@/components/shared/country-badge";
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
 * The event-centric Story browsing surface — filters (topic / publisher / lean), sort, and pagination
 * over the single Story Service (`/api/stories`). The Stories page renders this; Discover is the
 * article-centric surface (`DiscoverCard` over `/api/discover`). Facet values come from the catalog
 * (`/api/discover`). Opening a Story (its card) goes to the detail page, where each article opens its
 * canonical publisher URL.
 */
export function StoryBrowser({
  title,
  description,
  icon,
  defaultSort = "top",
  initialCountry,
  emptyDescription,
}: {
  title: string;
  description: string;
  icon: LucideIcon;
  defaultSort?: string;
  /** Preselects the country filter (deep link: /stories?country=US — e.g. the home place rail). */
  initialCountry?: string;
  emptyDescription: string;
}) {
  const { t, formatCompact } = useTranslation();
  const [topic, setTopic] = React.useState("all");
  const [publisher, setPublisher] = React.useState("all");
  const [lean, setLean] = React.useState("all");
  const [country, setCountry] = React.useState(initialCountry?.toUpperCase() ?? "all");
  const [sort, setSort] = React.useState(defaultSort);
  const [offset, setOffset] = React.useState(0);

  React.useEffect(() => {
    setOffset(0);
  }, [topic, publisher, lean, country, sort]);

  const facets = useDiscover({});
  // Located-catalog countries (Location Intelligence): the filter is only offered when located
  // articles exist, and only offers countries with data behind them — no dead options.
  const countries = useQuery({ queryKey: queryKeys.placeCountries, queryFn: services.placeCountries });
  const located = React.useMemo(
    () => (countries.data ?? []).filter((c) => c.articles > 0),
    [countries.data],
  );
  const countryOptions = React.useMemo(
    () => located.map((c) => ({ value: c.country, label: <CountryBadge code={c.country} /> })),
    [located],
  );
  const selectedCountry = React.useMemo(
    () => located.find((c) => c.country === country) ?? null,
    [located, country],
  );
  const { data, isLoading, isError, refetch, isFetching } = useStories({
    topic: asFilter(topic),
    publisher: asFilter(publisher),
    lean: asFilter(lean),
    country: asFilter(country),
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
            {total} {total === 1 ? "story" : "stories"}
          </span>
        )}
      </div>

      {/* Counted facts for the selected country (the former Countries-page overview, folded in):
          all three numbers come straight from the places facets already fetched for the picker. */}
      {selectedCountry && (
        <p className="-mt-3 mb-6 text-xs text-muted-foreground">
          {t("countries.stat.articles")} {formatCompact(selectedCountry.articles)} ·{" "}
          {t("countries.stat.publishers")} {formatCompact(selectedCountry.publishers)} ·{" "}
          {t("countries.stat.rated")} {formatCompact(selectedCountry.registryPublishers)}
        </p>
      )}

      {isLoading && (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-52 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && stories.length === 0 && (
        <EmptyState icon={icon} title={t("stories.empty.title")} description={emptyDescription} className="mt-4" />
      )}

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
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
