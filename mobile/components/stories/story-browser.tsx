import { router } from "expo-router";
import * as React from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { StoryQuery } from "@ih/core/domain/types";
import { sortByCountryName } from "@ih/core/logic/countries";

import { PageTitle } from "@/components/layout/screen";
import { CountryBadge } from "@/components/shared/country-badge";
import { FilterSelect, type FilterOption } from "@/components/shared/filter-select";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Icon, type IconName } from "@/components/ui/icon";
import { Skeleton } from "@/components/ui/skeleton";
import { Txt } from "@/components/ui/text";
import { radius } from "@/design/tokens";
import { useDiscover, usePlaceCountries, useStories } from "@/lib/hooks";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

import { StoryCard } from "./story-card";

const LEAN_OPTIONS: FilterOption[] = [
  { value: "left", label: "Left" },
  { value: "center", label: "Center" },
  { value: "right", label: "Right" },
];
const TYPE_VALUES = ["news", "research", "community"] as const;
const SORT_OPTIONS: FilterOption[] = [
  { value: "top", label: "Top" },
  { value: "latest", label: "Latest" },
  { value: "publishers", label: "Most sources" },
];
const opt = (values: string[]): FilterOption[] => values.map((v) => ({ value: v, label: v }));
const asFilter = (v: string) => (v === "all" ? undefined : v);
const PAGE = 24;

export interface BrowserParams {
  topic?: string;
  publisher?: string;
  lean?: string;
  country?: string;
  blindspot?: string;
  type?: string;
  tag?: string;
  from?: string;
  sort?: string;
}

/**
 * The event-centric Story browsing surface — filters (topic / publisher / covered-by / type /
 * country / gaps), sort, and pagination over the single Story Service. THE FILTERS LIVE IN THE
 * ROUTE PARAMS, not in component state — the native equivalent of the web keeping them in the URL,
 * so a filtered view survives opening a story and coming back, and every deep link (`?topic=`,
 * `?country=`, `?blindspot=any`, `?tag=`) arrives already applied.
 */
export function StoryBrowser({
  params,
  title,
  description,
  icon,
  defaultSort = "top",
  emptyDescription,
}: {
  params: BrowserParams;
  title: string;
  description: string;
  icon: IconName;
  defaultSort?: string;
  emptyDescription: string;
}) {
  const { t, formatCompact, lang } = useTranslation();
  const { palette } = useTheme();

  const topic = params.topic ?? "all";
  const publisher = params.publisher ?? "all";
  const lean = params.lean ?? "all";
  const country = params.country ? params.country.toUpperCase() : "all";
  const blindspot = params.blindspot ?? "all";
  const type = params.type ?? "all";
  const tag = params.tag ?? "all";
  const fromStory = params.from ?? "";
  const sort = params.sort ?? defaultSort;

  // A default is the ABSENCE of the param, so flipping a filter back to "all" leaves a clean route.
  const setParam = React.useCallback((key: keyof BrowserParams, value: string, whenDefault: string) => {
    router.setParams({ [key]: value === whenDefault ? undefined : value } as Record<string, string | undefined>);
  }, []);

  const [offset, setOffset] = React.useState(0);
  React.useEffect(() => {
    setOffset(0);
  }, [topic, publisher, lean, country, blindspot, type, tag, sort]);

  const facets = useDiscover({});
  const countries = usePlaceCountries();
  const selectedCountry = React.useMemo(() => (countries.data ?? []).find((c) => c.country === country) ?? null, [countries.data, country]);
  const { data, isLoading, isError, refetch, isFetching } = useStories({
    topic: asFilter(topic),
    publisher: asFilter(publisher),
    lean: asFilter(lean),
    country: asFilter(country),
    blindspot: asFilter(blindspot),
    type: asFilter(type),
    tag: asFilter(tag),
    fromStory: fromStory || undefined,
    sort: sort as StoryQuery["sort"],
    limit: PAGE,
    offset,
  });

  const tagLabel = React.useMemo(() => (data?.tagFacets ?? []).find((row) => row.tag === tag)?.label ?? tag, [data?.tagFacets, tag]);

  // Sticky facets: the response is briefly absent while a new filter loads, and a picker must not
  // unmount mid-interaction.
  const facetsRef = React.useRef<Record<string, number>>({});
  if (data?.countryFacets) facetsRef.current = data.countryFacets;
  const storyFacets = data?.countryFacets ?? facetsRef.current;
  const countryOptions = React.useMemo<FilterOption[]>(
    () => sortByCountryName(Object.keys(storyFacets), lang).map((code) => ({ value: code, label: <CountryBadge code={code} size={14} />, text: code })),
    [storyFacets, lang],
  );

  const typeRef = React.useRef<Record<string, number>>({});
  if (data?.typeFacets) typeRef.current = data.typeFacets;
  const typeFacets = data?.typeFacets ?? typeRef.current;
  const typeOptions = React.useMemo<FilterOption[]>(
    () => TYPE_VALUES.map((v) => ({ value: v, label: t(`filter.type.${v}`), count: typeFacets[v] })),
    [typeFacets, t],
  );

  const gapRef = React.useRef<Record<string, number>>({});
  if (data?.blindspotFacets) gapRef.current = data.blindspotFacets;
  const gapFacets = data?.blindspotFacets ?? gapRef.current;
  const blindspotOptions = React.useMemo<FilterOption[]>(() => {
    const sides = (["left", "center", "right"] as const).filter((s) => (gapFacets[s] ?? 0) > 0);
    if (sides.length === 0) return [];
    return [{ value: "any", label: t("filter.blindspot.any") }, ...sides.map((s) => ({ value: s, label: t(`filter.blindspot.${s}`) }))];
  }, [gapFacets, t]);

  const stories = data?.stories ?? [];
  const total = data?.total ?? 0;
  const page = data?.page ?? 1;
  const hasMore = data?.hasMore ?? false;

  return (
    <View>
      <PageTitle title={title} subtitle={description} />

      <View style={styles.filterBar}>
        <FilterSelect label={t("filter.topic")} value={topic} options={opt(facets.data?.topics ?? [])} onChange={(v) => setParam("topic", v, "all")} />
        <FilterSelect label={t("filter.publisher")} value={publisher} options={opt(facets.data?.publishers ?? [])} onChange={(v) => setParam("publisher", v, "all")} />
        <FilterSelect label={t("filter.coveredBy")} description={t("filter.coveredByHint")} value={lean} options={LEAN_OPTIONS} onChange={(v) => setParam("lean", v, "all")} />
        <FilterSelect label={t("filter.type")} description={t("filter.typeHint")} value={type} options={typeOptions} onChange={(v) => setParam("type", v, "all")} />
        {countryOptions.length > 0 && (
          <FilterSelect label={t("filter.country")} value={country} options={countryOptions} onChange={(v) => setParam("country", v, "all")} />
        )}
        {blindspotOptions.length > 0 && (
          <FilterSelect label={t("filter.blindspot")} value={blindspot} options={blindspotOptions} onChange={(v) => setParam("blindspot", v, "all")} />
        )}
        <FilterSelect label={t("filter.sort")} value={sort} options={SORT_OPTIONS} onChange={(v) => setParam("sort", v, defaultSort)} resettable={false} />
        {total > 0 && (
          <Txt size={14} muted tabular style={{ marginLeft: "auto" }}>
            {t("stories.count", { n: total })}
          </Txt>
        )}
      </View>

      {tag !== "all" && (
        <Pressable
          accessibilityRole="button"
          onPress={() => router.setParams({ tag: undefined, from: undefined } as Record<string, string | undefined>)}
          style={({ pressed }) => [styles.tagChip, { borderColor: palette.border, backgroundColor: pressed ? palette.accent : "transparent" }]}
        >
          <Txt size={12} weight="500">
            {t("filter.tag", { tag: tagLabel })}
          </Txt>
          <Icon name="x" size={14} />
        </Pressable>
      )}

      {selectedCountry && (
        <Txt size={12} muted style={{ marginTop: -12, marginBottom: 24 }}>
          {t("countries.stat.articles")} {formatCompact(selectedCountry.articles)} · {t("countries.stat.publishers")}{" "}
          {formatCompact(selectedCountry.publishers)} · {t("countries.stat.rated")} {formatCompact(selectedCountry.registryPublishers)}
        </Txt>
      )}

      {isLoading && (
        <View style={{ gap: 20 }} accessibilityElementsHidden>
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} height={208} />
          ))}
        </View>
      )}
      {isError && <ErrorState onRetry={() => void refetch()} />}

      {data && stories.length === 0 && (
        <EmptyState icon={icon} title={t("stories.empty.title")} description={emptyDescription} style={{ marginTop: 16 }} />
      )}

      <View style={{ gap: 20 }}>
        {stories.map((story) => (
          <StoryCard key={story.id} story={story} />
        ))}
      </View>

      {(page > 1 || hasMore) && (
        <View style={styles.pager}>
          <Button variant="outline" disabled={offset === 0 || isFetching} onPress={() => setOffset(Math.max(0, offset - PAGE))}>
            {t("common.previous")}
          </Button>
          <Txt size={14} muted>
            {t("common.page", { n: page })}
          </Txt>
          <Button variant="outline" disabled={!hasMore || isFetching} onPress={() => setOffset(offset + PAGE)}>
            {t("common.next")}
          </Button>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  filterBar: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8, marginBottom: 24 },
  tagChip: { flexDirection: "row", alignItems: "center", gap: 8, alignSelf: "flex-start", marginTop: -12, marginBottom: 24, borderWidth: 1, borderRadius: radius.pill, paddingHorizontal: 12, paddingVertical: 4 },
  pager: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 12, marginTop: 32 },
});
