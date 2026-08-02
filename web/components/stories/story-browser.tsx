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
  initialPublisher,
  initialBlindspot,
  emptyDescription,
}: {
  title: string;
  description: string;
  icon: LucideIcon;
  defaultSort?: string;
  /** Preselects the country filter (deep link: /stories?country=US — e.g. the home place rail). */
  initialCountry?: string;
  /** Preselects the publisher filter (deep link: /stories?publisher=NPR — the publisher page). */
  initialPublisher?: string;
  /** Preselects the coverage-gap lens (deep link: /stories?blindspot=any — the home module). */
  initialBlindspot?: string;
  emptyDescription: string;
}) {
  const { t, formatCompact } = useTranslation();
  const [topic, setTopic] = React.useState("all");
  const [publisher, setPublisher] = React.useState(initialPublisher ?? "all");
  const [lean, setLean] = React.useState("all");
  const [country, setCountry] = React.useState(initialCountry?.toUpperCase() ?? "all");
  const [blindspot, setBlindspot] = React.useState(initialBlindspot ?? "all");
  const [sort, setSort] = React.useState(defaultSort);
  const [offset, setOffset] = React.useState(0);

  React.useEffect(() => {
    setOffset(0);
  }, [topic, publisher, lean, country, blindspot, sort]);

  const facets = useDiscover({});
  // Article-level place facts (located articles / publishers / rated) for the facts line only —
  // the PICKER runs on story-level facets below, because a located article can be an unclustered
  // singleton: article facets cannot promise that a country returns any stories.
  const countries = useQuery({ queryKey: queryKeys.placeCountries, queryFn: services.placeCountries });
  const selectedCountry = React.useMemo(
    () => (countries.data ?? []).find((c) => c.country === country) ?? null,
    [countries.data, country],
  );
  const { data, isLoading, isError, refetch, isFetching } = useStories({
    topic: asFilter(topic),
    publisher: asFilter(publisher),
    lean: asFilter(lean),
    country: asFilter(country),
    blindspot: asFilter(blindspot),
    sort: sort as StoryQuery["sort"],
    limit: PAGE,
    offset,
  });

  // The picker's options: countries with ≥1 matching STORY (server-computed, country-filter
  // independent). Sticky across refetches — the response is briefly absent while a new filter
  // loads, and the control must not unmount mid-interaction.
  const facetsRef = React.useRef<Record<string, number>>({});
  if (data?.countryFacets) facetsRef.current = data.countryFacets;
  const storyFacets = data?.countryFacets ?? facetsRef.current;
  const countryOptions = React.useMemo(
    () =>
      Object.entries(storyFacets)
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
        .map(([code]) => ({ value: code, label: <CountryBadge code={code} /> })),
    [storyFacets],
  );

  // Coverage-gap lens options: only sides with ≥1 story under the other filters (same sticky
  // pattern as the country facets); the picker disappears entirely when no gaps are detected.
  const gapRef = React.useRef<Record<string, number>>({});
  if (data?.blindspotFacets) gapRef.current = data.blindspotFacets;
  const gapFacets = data?.blindspotFacets ?? gapRef.current;
  const blindspotOptions = React.useMemo(() => {
    const sides = (["left", "center", "right"] as const).filter((s) => (gapFacets[s] ?? 0) > 0);
    if (sides.length === 0) return [];
    return [
      { value: "any", label: t("filter.blindspot.any") },
      ...sides.map((s) => ({ value: s, label: t(`filter.blindspot.${s}`) })),
    ];
  }, [gapFacets, t]);

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
        {blindspotOptions.length > 0 && (
          <FilterSelect
            label={t("filter.blindspot")}
            value={blindspot}
            options={blindspotOptions}
            onChange={setBlindspot}
          />
        )}
        <FilterSelect label={t("filter.sort")} value={sort} options={SORT_OPTIONS} onChange={setSort} resettable={false} />
        {total > 0 && (
          <span className="ml-auto text-sm text-muted-foreground">
            {total === 1 ? t("stories.count.one", { n: total }) : t("stories.count.other", { n: total })}
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
          <StoryCard key={story.id} story={story} index={i} priority={i < 2} />
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
