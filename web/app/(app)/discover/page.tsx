"use client";

import * as React from "react";
import { Compass } from "lucide-react";
import { useDiscover } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import type { Article } from "@/types/domain";
import { PageContainer } from "@/components/layout/page-container";
import { CountryBadge } from "@/components/shared/country-badge";
import { DiscoverLeadCard } from "@/components/discover/discover-lead-card";
import { DiscoverRow } from "@/components/discover/discover-row";
import { FilterSelect, type FilterOption } from "@/components/shared/filter-select";
import { interleavePublishers } from "@/lib/discover-order";
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
  const hasMore = visible < total;

  // Front-page tier selection (Direction 1: "front page, then river"). Deterministic and cheap:
  // the LEAD is the newest article with a USABLE image among the first six (a front page leads
  // with its strongest fresh visual when one exists), else simply the newest; the two SUPPORTS
  // are the next articles from DIFFERENT publishers — one outlet's feed poll lands as a burst,
  // and recency-only ordering was putting the same masthead three times above the fold. When
  // diversity is impossible (a publisher filter is active), next-in-order fills.
  //
  // The river is publisher-INTERLEAVED (lib/discover-order.ts): the same burst problem below the
  // fold — measured, one outlet filing 6 of 12 visible rows — spread by the weakest rule that
  // fixes it (no two adjacent rows from one publisher while any alternative is pending). A
  // permutation of the full fetched list, computed once per fetch, so Load More reveals more of
  // a FIXED order and rows the reader has seen never move.
  const { lead, supports, river } = React.useMemo(() => {
    if (articles.length === 0) return { lead: null as Article | null, supports: [] as Article[], river: [] as Article[] };
    const usable = (a: Article) => Boolean(a.image) && !a.imageSuspect;
    const inFirstSix = articles.slice(0, 6).findIndex(usable);
    const leadIdx = inFirstSix >= 0 ? inFirstSix : 0;
    const leadArt = articles[leadIdx];
    if (!leadArt) return { lead: null, supports: [], river: [] }; // unreachable; typed indexing
    const rest = articles.filter((_, i) => i !== leadIdx);
    const picks: Article[] = [];
    for (const a of rest) {
      if (picks.length === 2) break;
      if (a.publisher !== leadArt.publisher && !picks.some((s) => s.publisher === a.publisher)) picks.push(a);
    }
    for (const a of rest) {
      if (picks.length === 2) break;
      if (!picks.includes(a)) picks.push(a);
    }
    const chosen = new Set([leadArt.id, ...picks.map((a) => a.id)]);
    return {
      lead: leadArt,
      supports: picks,
      river: interleavePublishers(articles.filter((a) => !chosen.has(a.id))),
    };
  }, [articles]);
  // Same reveal budget as before: `visible` counts front-page slots + river rows together.
  const riverShown = river.slice(0, Math.max(0, visible - (lead ? 1 + supports.length : 0)));

  return (
    // max-w-[88rem]: +10% over the shared max-w-7xl, Discover only — measured (headless width
    // experiment, 2026-08-16): river cards 602→666px eliminates clamp-truncation on long
    // headlines (the UFC-record sample lost its ellipsis) and improves break points, with zero
    // wrap regressions; gutters, breakpoints, and mobile (padding-bound) are untouched. The
    // thumb scales w-28→w-32 in DiscoverRow so the image share holds (~19%) — widening with a
    // fixed thumb made rows FEEL more compressed, not less (18.6%→16.8%).
    <PageContainer className="max-w-[88rem]">
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
      </div>

      {isLoading && (
        <div>
          <div className="grid gap-5 lg:grid-cols-3">
            <Skeleton className="h-96 rounded-lg lg:col-span-2" />
            <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-1">
              <Skeleton className="h-44 rounded-lg" />
              <Skeleton className="h-44 rounded-lg" />
            </div>
          </div>
          <div className="mt-5 grid gap-3 md:grid-cols-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-24 rounded-lg" />
            ))}
          </div>
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

      {/* Front page, then river (Direction 1, adopted 2026-08-16 — supersedes the masonry, whose
          height-blind round-robin traded the in-card void for a full card of column-end drift
          once adaptive cards made heights bimodal). The page's one job is discover-and-open: a
          lead tier makes the editorial claim, and the dense rows below serve the scan at 3-4x
          the old card density. The card/row itself is the Read affordance (whole-surface click,
          same recorded flow), Save is a quiet icon, and lean is said once as the pill. The old
          card component lives on in Search's masonry, unchanged. */}
      {lead && (
        <div className="grid gap-5 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <DiscoverLeadCard article={lead} size="lead" priority />
          </div>
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-1">
            {supports.map((a, i) => (
              <DiscoverLeadCard key={a.id} article={a} size="support" priority={i === 0} index={i + 1} />
            ))}
          </div>
        </div>
      )}
      {riverShown.length > 0 && (
        <div className="mt-5 grid gap-3 md:grid-cols-2">
          {riverShown.map((a) => (
            <DiscoverRow key={a.id} article={a} />
          ))}
        </div>
      )}

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
