"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Globe2 } from "lucide-react";
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
 * Countries — browse the located catalog by country (Location Intelligence 1.5).
 *
 * Everything is a counted fact from the platform: the country list is the union of located
 * catalog coverage and the curated registry (registry-only countries show honest zeros), the
 * overview numbers are counts, the latest coverage is the same search surface filtered by
 * country, and the publisher panel is registry facts. No maps, no event locations — those are
 * Phase 2+ by design.
 */
export default function CountriesPage() {
  const { t, formatCompact } = useTranslation();
  const [country, setCountry] = React.useState<string | null>(null);

  const facets = useQuery({ queryKey: queryKeys.placeCountries, queryFn: services.placeCountries });
  const selected = React.useMemo(
    () => (facets.data ?? []).find((c) => c.country === country) ?? null,
    [facets.data, country],
  );

  const publishers = useQuery({
    queryKey: queryKeys.placePublishers(country ? { country } : undefined),
    queryFn: () => services.placePublishers(country ? { country } : undefined),
    enabled: country != null,
  });
  const articles = useSearch({ country: country ?? undefined, sort: "newest", limit: 12 }, country != null);

  return (
    <PageContainer>
      <PageHeader title={t("countries.title")} description={t("countries.subtitle")} />

      <div
        role="toolbar"
        aria-label={t("countries.pick")}
        className="-mx-1 mb-6 flex gap-2 overflow-x-auto px-1 pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {(facets.data ?? []).map((c) => (
          <FilterChip
            key={c.country}
            label={<CountryBadge code={c.country} />}
            count={c.articles}
            active={country === c.country}
            onClick={() => setCountry(country === c.country ? null : c.country)}
          />
        ))}
      </div>

      {facets.isLoading && <Skeleton className="h-96 w-full rounded-lg" />}
      {facets.isError && <ErrorState onRetry={() => facets.refetch()} />}

      {facets.data && country == null && (
        <EmptyState icon={Globe2} title={t("countries.empty.title")} description={t("countries.empty.body")} />
      )}

      {facets.data && country != null && (
        <PageGrid
          rail={
            <section aria-labelledby="country-publishers-heading" className="rounded-lg border bg-card p-4">
              <SectionHeader
                id="country-publishers-heading"
                title={t("story.publishersTitle")}
                className="mb-3"
              />
              {publishers.isLoading && <Skeleton className="h-40 w-full rounded-lg" />}
              {publishers.data && publishers.data.length === 0 && (
                <p className="text-sm text-muted-foreground">{t("countries.noRated")}</p>
              )}
              {publishers.data && publishers.data.length > 0 && (
                <ul className="divide-y">
                  {publishers.data.map((p) => (
                    <li key={p.name} className="flex flex-wrap items-center gap-x-2 gap-y-1 py-2.5">
                      <span className="min-w-0 flex-1 truncate text-sm font-medium">{p.name}</span>
                      <LeanBadge lean={p.lean} bucket={p.leanBucket} />
                      {p.scope && (
                        <span className="rounded-full bg-muted px-2 py-0.5 text-[0.68rem] font-medium text-muted-foreground">
                          {t(`local.scope.${p.scope}`)}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-3 border-t pt-3 text-xs text-muted-foreground">{t("local.registryNote")}</p>
            </section>
          }
        >
          {/* Overview — counted facts for the selected country. */}
          <section aria-labelledby="country-overview-heading" className="rounded-lg border bg-card p-4">
            <SectionHeader
              id="country-overview-heading"
              title={countryName(country, activeLang())}
              className="mb-3"
            />
            <div className="grid grid-cols-3 gap-3">
              <Stat label={t("countries.stat.articles")} value={formatCompact(selected?.articles ?? 0)} />
              <Stat label={t("countries.stat.publishers")} value={formatCompact(selected?.publishers ?? 0)} />
              <Stat label={t("countries.stat.rated")} value={formatCompact(selected?.registryPublishers ?? 0)} />
            </div>
          </section>

          <section aria-labelledby="country-latest-heading">
            <SectionHeader
              id="country-latest-heading"
              title={t("local.articles", { place: countryName(country, activeLang()) })}
            />
            {articles.isLoading && <Skeleton className="h-64 w-full rounded-lg" />}
            {articles.data && articles.data.results.length === 0 && (
              <EmptyState
                icon={Globe2}
                title={t("local.noArticles.title")}
                description={t("local.noArticles.body")}
              />
            )}
            {articles.data && articles.data.results.length > 0 && (
              <div className="space-y-3">
                {articles.data.results.map((a, i) => (
                  <ArticleRow key={a.id} article={a} index={i} />
                ))}
              </div>
            )}
          </section>
        </PageGrid>
      )}
    </PageContainer>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[0.68rem] text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-lg font-semibold tabular-nums tracking-tight">{value}</p>
    </div>
  );
}
