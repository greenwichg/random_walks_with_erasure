"use client";

import * as React from "react";
import { Compass } from "lucide-react";
import { useDiscover } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { PageContainer } from "@/components/layout/page-container";
import { CountryBadge } from "@/components/shared/country-badge";
import { DiscoverCard } from "@/components/discover/discover-card";
import { FilterSelect, type FilterOption } from "@/components/shared/filter-select";
import { FilterBar } from "@/components/shared/filter-bar";
import { interleavePublishers } from "@ih/core/logic/discover-order";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";

// Discover is the ARTICLE-centric surface: the latest individual FeedArticles from the live catalog
// (/api/discover), newest first, with topic/publisher/covered-by filters. Stories is the event-centric
// surface (clustered coverage) — the two are deliberately distinct. Each card opens the real
// publisher URL via the shared Read flow. No Story Service / clustering involved here.

const LEAN_OPTIONS: FilterOption[] = [
  { value: "left", label: "Left" },
  { value: "center", label: "Center" },
  { value: "right", label: "Right" },
];
/** Curated SOURCE type, from the outlet registry's `kind` column — see outlet_registry.source_type.
 *  A fixed vocabulary, so a fixed list like LEAN_OPTIONS rather than a data-derived one. On this
 *  surface an article has ONE publisher, so it simply is or is not that type. */
const TYPE_VALUES = ["news", "research", "community"] as const;
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
  const [type, setType] = React.useState("all");
  // "Load More", not pages and not infinite scroll: the browse stream reads continuously, but
  // continuing is the READER'S deliberate act — an information-diet product doesn't autoload
  // the bottomless feed it exists to push back against. The whole capped set is already
  // fetched, so revealing more costs zero requests.
  const [visible, setVisible] = React.useState(PAGE);

  // Any filter change resets to the first batch.
  React.useEffect(() => {
    setVisible(PAGE);
  }, [topic, publisher, lean, country, type]);

  const { data, isLoading, isError, refetch } = useDiscover({
    topic: asFilter(topic),
    publisher: asFilter(publisher),
    lean: asFilter(lean),
    country: asFilter(country),
    type: asFilter(type),
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

  const typeOptions = React.useMemo(
    () => TYPE_VALUES.map((v) => ({ value: v, label: t(`filter.type.${v}`) })),
    [t],
  );

  const articles = data?.articles ?? [];
  const total = articles.length;
  // Publisher interleave (kept through the layout revert, 2026-08-16): one outlet's feed poll
  // lands as a burst, and recency-only order rendered it verbatim — measured, one publisher on
  // 6 of 12 visible cards. A permutation of the full fetched list, computed once per fetch, so
  // Load More reveals more of a FIXED order and cards the reader has seen never move.
  const ordered = React.useMemo(() => interleavePublishers(articles), [articles]);
  const shown = ordered.slice(0, visible);
  const hasMore = visible < total;

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">{t("discover.title")}</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">{t("discover.subtitle")}</p>
      </div>

      {/* The count is the filters' answer, said where Stories and Search say theirs. */}
      <FilterBar trailing={total > 0 ? t("stories.articlesCount", { n: total }) : undefined}>
        <FilterSelect label={t("filter.topic")} value={topic} options={opt(data?.topics ?? [])} onChange={setTopic} />
        <FilterSelect
          label={t("filter.publisher")}
          value={publisher}
          options={opt(data?.publishers ?? [])}
          onChange={setPublisher}
        />
        <FilterSelect label={t("filter.coveredBy")} description={t("filter.coveredByHint")}
          value={lean} options={LEAN_OPTIONS} onChange={setLean} />
        <FilterSelect label={t("filter.type")} description={t("filter.typeHint")}
          value={type} options={typeOptions} onChange={setType} />
        {countryOptions.length > 0 && (
          <FilterSelect label={t("filter.country")} value={country} options={countryOptions} onChange={setCountry} />
        )}
      </FilterBar>

      {isLoading && (
        /* Uniform tall skeletons — an honest preview: every card carries the occupied image
           slot, so real cards land at near-identical heights in a stretch grid. */
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-[480px] rounded-lg" />
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

      {/* Uniform grid over uniform cards (2026-08-23). The 2026-08-16 uniform grid was right for
          near-uniform cards and failed only because the cards feeding it were bimodal — image
          cards ran ~2.3× text-first cards, and the stretch rendered the difference as dead
          space. A masonry interlude compensated in placement; the real fix landed in the CARD:
          the always-occupied image slot (publisher placeholder when art is absent, suspect, or
          broken — see DiscoverCard) removes the bimodality at the source, so the masonry
          machinery was retired and the grid returned. Rows stretch a few px at most now, the
          card's single slack point absorbs it invisibly, and the grid keeps what masonry traded
          away: exact row-major reading order, and DOM order matching visual order for keyboard
          and screen readers. Unchanged throughout: the imageSuspect/branding guard (DiscoverCard),
          display-title hygiene (engine), publisher interleave (above), the visible lean-pill
          tint (Badge variants), lean-said-once (leanDot={false} here), and the shared read/save
          flows. */}
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
        {shown.map((article, i) => (
          <DiscoverCard key={article.id} article={article} index={i % PAGE} priority={i < 2} leanDot={false} />
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
