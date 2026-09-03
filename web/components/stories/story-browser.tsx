"use client";

import * as React from "react";
import type { LucideIcon } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useStories, useDiscover } from "@/hooks/use-data";
import { services, queryKeys } from "@ih/core/api/services";
import { useTranslation } from "@/lib/i18n";
import type { StoryQuery } from "@ih/core/domain/types";
import { PageContainer } from "@/components/layout/page-container";
import { StoryCard } from "@/components/stories/story-card";
import { FilterSelect, type FilterOption } from "@/components/shared/filter-select";
import { FilterBar } from "@/components/shared/filter-bar";
import { CountryBadge } from "@/components/shared/country-badge";
import { sortByCountryName } from "@ih/core/logic/countries";
import { activeLang } from "@/lib/active-lang";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";

const LEAN_OPTIONS: FilterOption[] = [
  { value: "left", label: "Left" },
  { value: "center", label: "Center" },
  { value: "right", label: "Right" },
];
/**
 * Curated SOURCE type. A fixed list rather than a facet-gated one, because these three are the
 * registry's whole reader-facing vocabulary and do not vary with the catalog.
 *
 * The values are the engine's, not new UI concepts: `outlet_registry.source_type` projects them
 * from the curated `kind` column — no kind is a news outlet, `research` a journal or preprint
 * server, `forum` user-generated posts. A story matches when at least one of its publishers is
 * curated that way, which is the same "has coverage from" reading the Covered-by lens already uses.
 */
const TYPE_VALUES = ["news", "research", "community"] as const;
const SORT_OPTIONS: FilterOption[] = [
  { value: "top", label: "Top" },
  { value: "latest", label: "Latest" },
  { value: "publishers", label: "Most sources" },
];
const opt = (values: string[]): FilterOption[] => values.map((v) => ({ value: v, label: v }));
const asFilter = (v: string) => (v === "all" ? undefined : v);
const PAGE = 24;

/**
 * The event-centric Story browsing surface — filters (topic / publisher / covered-by), sort, and
 * pagination
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

  // THE FILTERS LIVE IN THE URL, not in component state.
  //
  // They used to be six `useState`s, which meant a selection existed only for as long as this
  // component stayed mounted. Opening a story unmounts it; coming back mounts a fresh one, and
  // every filter is its initial value again. Three of them (country / publisher / blindspot) were
  // already read FROM the URL as deep-link entry points, but nothing ever wrote back — so even
  // those reset the moment the reader changed them by hand rather than arriving via a link.
  //
  // The URL is the state the browser already restores on Back, so putting them there is the fix
  // and the smallest one: no store, no context, no session storage. It also makes a filtered view
  // shareable and bookmarkable, which the three deep links imply was the intent all along.
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  // A prop is the fallback when its param is absent, so the existing deep links and `defaultSort`
  // behave exactly as before and a bare /stories still loads defaults.
  const rawCountry = params.get("country") ?? initialCountry;
  const topic = params.get("topic") ?? "all";
  const publisher = params.get("publisher") ?? initialPublisher ?? "all";
  const lean = params.get("lean") ?? "all";
  // Only uppercase a real code: "all".toUpperCase() is "ALL", which `asFilter` would send to the
  // engine as a country named ALL instead of meaning "no country filter".
  const country = rawCountry ? rawCountry.toUpperCase() : "all";
  const blindspot = params.get("blindspot") ?? initialBlindspot ?? "all";
  const type = params.get("type") ?? "all";
  const sort = params.get("sort") ?? defaultSort;

  const setParam = React.useCallback(
    (key: string, value: string, whenDefault: string) => {
      const next = new URLSearchParams(params.toString());
      // A default is the ABSENCE of the param, so flipping a filter back to "all" leaves a clean
      // URL rather than /stories?topic=all&lean=all&…
      if (value === whenDefault) next.delete(key);
      else next.set(key, value);
      const qs = next.toString();
      // `replace`, not `push`: each filter change edits the entry the reader is standing on. With
      // `push`, Back would have to walk them one at a time through every tweak they made before it
      // finally left the page — which is a worse bug than the one being fixed here.
      // `scroll: false` keeps the list where it is instead of jumping to the top on every change.
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  const setTopic = React.useCallback((v: string) => setParam("topic", v, "all"), [setParam]);
  const setPublisher = React.useCallback((v: string) => setParam("publisher", v, "all"), [setParam]);
  const setLean = React.useCallback((v: string) => setParam("lean", v, "all"), [setParam]);
  const setCountry = React.useCallback((v: string) => setParam("country", v, "all"), [setParam]);
  const setBlindspot = React.useCallback((v: string) => setParam("blindspot", v, "all"), [setParam]);
  const setType = React.useCallback((v: string) => setParam("type", v, "all"), [setParam]);
  const setSort = React.useCallback(
    (v: string) => setParam("sort", v, defaultSort), [setParam, defaultSort]);

  const [offset, setOffset] = React.useState(0);

  React.useEffect(() => {
    setOffset(0);
  }, [topic, publisher, lean, country, blindspot, type, sort]);


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
    type: asFilter(type),
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
  // Ordered by the DISPLAY NAME the reader is actually reading, not by story count: a list you
  // scan for a known country has to be alphabetical, or finding "Japan" means reading all of it.
  // `localeCompare` with the active language so accented names sort where that language expects
  // (Å after A in English, its own letter in Swedish) rather than by code point.
  // "All" is not in this list — FilterSelect renders its reset row above the options.
  const countryOptions = React.useMemo(
    () =>
      sortByCountryName(Object.keys(storyFacets), activeLang()).map((code) => ({
        value: code,
        label: <CountryBadge code={code} />,
      })),
    [storyFacets],
  );

  // Source-type options: the fixed vocabulary, each carrying what selecting it would return.
  // Same sticky pattern as the two pickers around it — the response is briefly absent while a new
  // filter loads, and a count that blinked to zero mid-interaction would be a lie about the data
  // rather than a loading state.
  //
  // Unlike country and coverage gaps, an empty type is NOT dropped: three fixed options that
  // appear and disappear between page states read as a broken control, and the badge already
  // answers what omission was there to answer — "Community 0" tells the reader not to spend the
  // click, and tells them the lens exists.
  const typeRef = React.useRef<Record<string, number>>({});
  if (data?.typeFacets) typeRef.current = data.typeFacets;
  const typeFacets = data?.typeFacets ?? typeRef.current;
  const typeOptions = React.useMemo(
    () => TYPE_VALUES.map((v) => ({ value: v, label: t(`filter.type.${v}`), count: typeFacets[v] })),
    [typeFacets, t],
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

      <FilterBar
        trailing={
          total > 0 ? t("stories.count", { n: total }) : undefined
        }
      >
        <FilterSelect label={t("filter.topic")} value={topic} options={opt(facets.data?.topics ?? [])} onChange={setTopic} />
        <FilterSelect
          label={t("filter.publisher")}
          value={publisher}
          options={opt(facets.data?.publishers ?? [])}
          onChange={setPublisher}
        />
        <FilterSelect label={t("filter.coveredBy")} description={t("filter.coveredByHint")}
          value={lean} options={LEAN_OPTIONS} onChange={setLean} />
        <FilterSelect label={t("filter.type")} description={t("filter.typeHint")}
          value={type} options={typeOptions} onChange={setType} />
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
      </FilterBar>

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
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-52 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && stories.length === 0 && (
        <EmptyState icon={icon} title={t("stories.empty.title")} description={emptyDescription} className="mt-4" />
      )}

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
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
