"use client";

import * as React from "react";
import { Compass } from "lucide-react";
import { useDiscover } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { PageContainer } from "@/components/layout/page-container";
import { CountryBadge } from "@/components/shared/country-badge";
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
  const { t } = useTranslation();
  const [topic, setTopic] = React.useState("all");
  const [publisher, setPublisher] = React.useState("all");
  const [lean, setLean] = React.useState("all");
  // Default "all" = Global: the request carries no country param and behaves exactly as before.
  const [country, setCountry] = React.useState("all");
  // "Load More", not pages and not infinite scroll: the browse stream reads continuously, but
  // continuing is the READER'S deliberate act — an information-diet product doesn't autoload
  // the bottomless feed it exists to push back against. The whole capped set is already
  // fetched, so revealing more costs zero requests.
  const [visible, setVisible] = React.useState(PAGE);

  // Any filter change resets to the first batch.
  React.useEffect(() => {
    setVisible(PAGE);
  }, [topic, publisher, lean, country]);

  const { data, isLoading, isError, refetch } = useDiscover({
    topic: asFilter(topic),
    publisher: asFilter(publisher),
    lean: asFilter(lean),
    country: asFilter(country),
    limit: FETCH,
  });

  // Country options: countries with ≥1 located article on this surface (server-computed,
  // country-filter-independent — same semantics as the Stories picker). Sticky across refetches
  // so the control never unmounts mid-interaction; hidden entirely until event geography flows.
  const facetsRef = React.useRef<Record<string, number>>({});
  if (data?.countryFacets) facetsRef.current = data.countryFacets;
  const countryFacets = data?.countryFacets ?? facetsRef.current;
  const countryOptions = React.useMemo(
    () =>
      Object.entries(countryFacets)
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
        .map(([code]) => ({ value: code, label: <CountryBadge code={code} /> })),
    [countryFacets],
  );

  const articles = data?.articles ?? [];
  const total = articles.length;
  const shown = articles.slice(0, visible);
  const hasMore = visible < total;

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">{t("discover.title")}</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">{t("discover.subtitle")}</p>
      </div>

      <div className="mb-6 flex flex-wrap items-center gap-2">
        <FilterSelect label={t("filter.topic")} value={topic} options={opt(data?.topics ?? [])} onChange={setTopic} />
        <FilterSelect
          label={t("filter.publisher")}
          value={publisher}
          options={opt(data?.publishers ?? [])}
          onChange={setPublisher}
        />
        <FilterSelect label={t("filter.lean")} value={lean} options={LEAN_OPTIONS} onChange={setLean} />
        {countryOptions.length > 0 && (
          <FilterSelect label={t("filter.country")} value={country} options={countryOptions} onChange={setCountry} />
        )}
        {total > 0 && (
          <span className="ml-auto text-sm text-muted-foreground">
            {total === 1 ? t("discover.count.one", { n: total }) : t("discover.count.other", { n: total })}
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

      {data && total === 0 && (
        <EmptyState
          icon={Compass}
          title={t("discover.empty.title")}
          description={t("discover.empty.body")}
          className="mt-4"
        />
      )}

      {/* Uniform-height grid, deliberately not masonry: grid rows stretch every card in a row to
          the same height and the card's internal flex slack absorbs the difference — simple,
          consistent rows over packed columns. (Search/Saved keep MasonryColumns.) */}
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
        {shown.map((article, i) => (
          <DiscoverCard key={article.id} article={article} index={i % PAGE} />
        ))}
      </div>

      {hasMore && (
        <div className="mt-4 flex justify-center">
          <button
            onClick={() => setVisible((v) => v + PAGE)}
            className="inline-flex h-9 items-center rounded-lg border bg-card px-4 text-sm font-medium transition-colors hover:bg-accent"
          >
            {t("common.loadMore")}
          </button>
        </div>
      )}
    </PageContainer>
  );
}
