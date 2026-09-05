import { useLocalSearchParams } from "expo-router";
import * as React from "react";
import { StyleSheet, TextInput, View } from "react-native";

import type { SearchParams } from "@ih/core/domain/types";

import { DiscoverCard } from "@/components/discover/discover-card";
import { PageTitle, Screen } from "@/components/layout/screen";
import { CountryBadge } from "@/components/shared/country-badge";
import { FilterSelect, type FilterOption } from "@/components/shared/filter-select";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icon";
import { Skeleton } from "@/components/ui/skeleton";
import { Txt } from "@/components/ui/text";
import { fontFamily } from "@/design/fonts";
import { radius } from "@/design/tokens";
import { useDiscover, usePlaceCountries, useSearch } from "@/lib/hooks";
import { useTheme } from "@/lib/theme";
import { useTranslation } from "@/lib/i18n-context";

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

/**
 * Search — the mobile web's `/search` page (below `sm` there is no header search control; this IS
 * the Search tab): the query field, the filter row (topic · publisher · covered-by · country ·
 * sort), the result cards and the pager. `?query=` and `?publisher=` deep links arrive pre-filled.
 */
export default function SearchScreen() {
  const { t } = useTranslation();
  const { palette } = useTheme();
  const params = useLocalSearchParams<{ query?: string; publisher?: string }>();
  const [q, setQ] = React.useState(params.query ?? "");
  const [topic, setTopic] = React.useState("all");
  const [publisher, setPublisher] = React.useState(params.publisher ?? "all");
  const [lean, setLean] = React.useState("all");
  const [country, setCountry] = React.useState("all");
  const [sort, setSort] = React.useState("newest");
  const [offset, setOffset] = React.useState(0);

  React.useEffect(() => {
    if (params.publisher) setPublisher(params.publisher);
  }, [params.publisher]);
  React.useEffect(() => {
    if (params.query != null) setQ(params.query);
  }, [params.query]);

  React.useEffect(() => {
    setOffset(0);
  }, [q, topic, publisher, lean, country, sort]);

  const facets = useDiscover({});
  const countries = usePlaceCountries();
  const countryOptions = React.useMemo<FilterOption[]>(
    () =>
      (countries.data ?? [])
        .filter((c) => c.articles > 0)
        .map((c) => ({ value: c.country, label: <CountryBadge code={c.country} size={14} />, text: c.country })),
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
    <Screen>
      <PageTitle title={t("search.title")} subtitle={t("search.subtitle")} />

      <View style={[styles.field, { borderColor: palette.border, backgroundColor: palette.card }]}>
        <Icon name="search" size={16} color={palette.mutedForeground} />
        <TextInput
          value={q}
          onChangeText={setQ}
          placeholder={t("search.placeholder")}
          placeholderTextColor={palette.mutedForeground}
          autoCapitalize="none"
          autoCorrect={false}
          returnKeyType="search"
          accessibilityLabel={t("search.title")}
          style={[styles.input, { color: palette.foreground, fontFamily: fontFamily("400") }]}
        />
      </View>

      <View style={styles.filterBar}>
        <FilterSelect label={t("filter.topic")} value={topic} options={opt(facets.data?.topics ?? [])} onChange={setTopic} />
        <FilterSelect label={t("filter.publisher")} value={publisher} options={opt(facets.data?.publishers ?? [])} onChange={setPublisher} />
        <FilterSelect label={t("filter.coveredBy")} description={t("filter.coveredByHint")} value={lean} options={LEAN_OPTIONS} onChange={setLean} />
        {countryOptions.length > 0 && <FilterSelect label={t("filter.country")} value={country} options={countryOptions} onChange={setCountry} />}
        <FilterSelect label={t("filter.sort")} value={sort} options={SORT_OPTIONS} onChange={setSort} resettable={false} />
        {total > 0 && (
          <Txt size={14} muted tabular style={{ marginLeft: "auto" }}>
            {t("common.results", { n: total })}
          </Txt>
        )}
      </View>

      {isLoading && (
        <View style={{ gap: 20 }} accessibilityElementsHidden>
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} height={224} />
          ))}
        </View>
      )}
      {isError && <ErrorState onRetry={() => void refetch()} />}

      {data && results.length === 0 && (
        <EmptyState icon="search" title={t("search.empty.title")} description={t("search.empty.body")} style={{ marginTop: 16 }} />
      )}

      <View style={{ gap: 20 }}>
        {results.map((article) => (
          <DiscoverCard key={article.id} article={article} openedFrom="search" />
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
    </Screen>
  );
}

const styles = StyleSheet.create({
  field: { flexDirection: "row", alignItems: "center", gap: 8, borderWidth: StyleSheet.hairlineWidth, borderRadius: radius.lg, paddingHorizontal: 12, marginBottom: 16 },
  input: { flex: 1, height: 44, fontSize: 16, paddingVertical: 0 },
  filterBar: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8, marginBottom: 24 },
  pager: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 12, marginTop: 32 },
});
