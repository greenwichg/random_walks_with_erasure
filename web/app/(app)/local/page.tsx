"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { MapPin } from "lucide-react";
import { services, queryKeys } from "@/services";
import { useSearch } from "@/hooks/use-data";
import { PageContainer, PageHeader } from "@/components/layout/page-container";
import { PageGrid } from "@/components/layout/page-grid";
import { SectionHeader } from "@/components/shared/section-header";
import { ArticleRow } from "@/components/shared/article-row";
import { LeanBadge } from "@/components/shared/article-badges";
import { FilterChip } from "@/components/ui/filter-chip";
import { CountryBadge } from "@/components/shared/country-badge";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";
import { useTranslation } from "@/lib/i18n";
import { activeLang } from "@/lib/i18n-core";
import { countryName } from "@/lib/countries";

/**
 * Local News v1 — publisher locality only (Location Intelligence Phase 1).
 *
 * Answers exactly one question — "which publishers are local to the selected place?" — from the
 * curated locality registry, plus the located catalog's articles for that place. No event
 * locations, no coordinates, no GPS, no inference: everything on this page is either a registry
 * fact or a stored article field. As the registry gains regional/local outlets and Phase 2 adds
 * event geography, this page deepens without changing shape.
 */
export default function LocalPage() {
  const { t, formatCompact } = useTranslation();
  const [country, setCountry] = React.useState<string | null>(null);

  const publishers = useQuery({
    queryKey: queryKeys.placePublishers(),
    queryFn: () => services.placePublishers(),
  });

  // Countries actually present in the registry — the picker never offers a place with no data.
  const countries = React.useMemo(() => {
    const counts = new Map<string, number>();
    for (const p of publishers.data ?? []) {
      if (p.country) counts.set(p.country, (counts.get(p.country) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [publishers.data]);

  const visible = React.useMemo(
    () => (publishers.data ?? []).filter((p) => country === null || p.country === country),
    [publishers.data, country],
  );

  // The located catalog for the selected place — the same search surface, filtered by country.
  const articles = useSearch({ country: country ?? undefined, sort: "newest", limit: 12 }, country != null);

  return (
    <PageContainer>
      <PageHeader title={t("local.title")} description={t("local.subtitle")} />

      <div
        role="toolbar"
        aria-label={t("local.pickCountry")}
        className="-mx-1 mb-6 flex gap-2 overflow-x-auto px-1 pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        <FilterChip label={t("home.trending.all")} active={country === null} onClick={() => setCountry(null)} />
        {countries.map(([code, n]) => (
          <FilterChip
            key={code}
            label={<CountryBadge code={code} />}
            count={n}
            active={country === code}
            onClick={() => setCountry(country === code ? null : code)}
          />
        ))}
      </div>

      {publishers.isLoading && (
        <div aria-hidden>
          <PageGrid rail={<Skeleton className="h-72 w-full rounded-lg" />}>
            <Skeleton className="h-96 w-full rounded-lg" />
          </PageGrid>
        </div>
      )}
      {publishers.isError && <ErrorState onRetry={() => publishers.refetch()} />}

      {publishers.data && (
        <PageGrid
          rail={
            <section aria-labelledby="local-publishers-heading" className="rounded-lg border bg-card p-4">
              <SectionHeader
                id="local-publishers-heading"
                title={t("local.publishers", { n: formatCompact(visible.length) })}
                className="mb-3"
              />
              <ul className="divide-y">
                {visible.map((p) => (
                  <li key={p.name} className="flex flex-wrap items-center gap-x-2 gap-y-1 py-2.5">
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">{p.name}</span>
                    <LeanBadge lean={p.lean} bucket={p.leanBucket} />
                    {p.scope && (
                      <span className="rounded-full bg-muted px-2 py-0.5 text-[0.68rem] font-medium text-muted-foreground">
                        {t(`local.scope.${p.scope}`)}
                      </span>
                    )}
                    {(p.city || p.region || p.country) && (
                      <span className="w-full text-[0.7rem] text-muted-foreground">
                        {[p.city, p.region, p.country].filter(Boolean).join(" · ")}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
              <p className="mt-3 border-t pt-3 text-xs text-muted-foreground">{t("local.registryNote")}</p>
            </section>
          }
        >
          {country == null ? (
            <EmptyState icon={MapPin} title={t("local.empty.title")} description={t("local.empty.body")} />
          ) : (
            <section aria-labelledby="local-articles-heading">
              <SectionHeader
                id="local-articles-heading"
                title={t("local.articles", { place: countryName(country, activeLang()) })}
              />
              {articles.isLoading && <Skeleton className="h-64 w-full rounded-lg" />}
              {articles.data && articles.data.results.length === 0 && (
                <EmptyState icon={MapPin} title={t("local.noArticles.title")} description={t("local.noArticles.body")} />
              )}
              {articles.data && articles.data.results.length > 0 && (
                <div className="space-y-3">
                  {articles.data.results.map((a, i) => (
                    <ArticleRow key={a.id} article={a} index={i} />
                  ))}
                </div>
              )}
            </section>
          )}
        </PageGrid>
      )}
    </PageContainer>
  );
}
