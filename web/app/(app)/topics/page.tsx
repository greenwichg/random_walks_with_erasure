"use client";

import * as React from "react";
import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { queryKeys, services } from "@ih/core/api/services";
import { useDiscover } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { CountryBadge } from "@/components/shared/country-badge";
import { FollowButton } from "@/components/shared/follow-button";
import { PublisherLogo } from "@/components/shared/publisher-logo";
import { Skeleton } from "@/components/ui/skeleton";
import { useTranslation } from "@/lib/i18n";

/**
 * Discover topics — the reference's browse-and-follow index, over the three things Hidden View
 * actually holds: the catalog's TOPICS, the PLACES with coverage behind them, and the SOURCES in
 * the registry.
 *
 * What each row can do is decided by what backs it, not by symmetry:
 *   topics  — follow writes an Interest Intensity slider, so only the eight topics with a slider
 *             behind them carry the toggle; the rest are still links to their coverage.
 *   places  — follow writes `Settings.locations`, the same list Settings > Places writes.
 *   sources — no follow contract exists for a publisher, so these are links to the publisher's
 *             counted profile and nothing more.
 *
 * The reference's fourth section, People, has no counterpart: there are no person profiles in the
 * engine, so it is absent rather than faked.
 */

const SHOW = 6;

function SectionHead({
  title,
  expanded,
  onToggle,
  canExpand,
}: {
  title: string;
  expanded: boolean;
  onToggle: () => void;
  canExpand: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="mb-2 flex items-baseline justify-between gap-3">
      <h2 className="text-[21px] font-bold leading-tight tracking-tight">{title}</h2>
      {canExpand && (
        <button
          type="button"
          onClick={onToggle}
          className="shrink-0 rounded text-[13px] font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {expanded ? t("common.previous") : t("topics.showAll")}
        </button>
      )}
    </div>
  );
}

/** One browse row: a leading mark, the name, then whatever control the row's data can back. */
function BrowseRow({
  href,
  mark,
  label,
  sublabel,
  action,
}: {
  href: string;
  mark?: React.ReactNode;
  label: React.ReactNode;
  sublabel?: string;
  action?: React.ReactNode;
}) {
  return (
    <li className="flex items-center gap-3 border-b border-border/70 last:border-b-0">
      <Link
        href={href}
        className="flex min-w-0 flex-1 items-center gap-3 py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        {mark && <span className="grid h-9 w-9 shrink-0 place-items-center overflow-hidden rounded-full border bg-muted">{mark}</span>}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[15px] font-medium">{label}</span>
          {sublabel && <span className="block truncate text-[11px] text-muted-foreground">{sublabel}</span>}
        </span>
        {!action && <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />}
      </Link>
      {action}
    </li>
  );
}

export default function TopicsPage() {
  const { t, formatCompact } = useTranslation();
  const facets = useDiscover({});
  const countries = useQuery({ queryKey: queryKeys.placeCountries, queryFn: services.placeCountries });
  const [open, setOpen] = React.useState<Record<string, boolean>>({});
  const toggle = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }));

  const topics = facets.data?.topics ?? [];
  const publishers = facets.data?.publishers ?? [];
  const places = React.useMemo(
    () => (countries.data ?? []).filter((c) => c.articles > 0).sort((a, b) => b.articles - a.articles),
    [countries.data],
  );

  const slice = <T,>(list: T[], key: string) => (open[key] ? list : list.slice(0, SHOW));

  return (
    <PageContainer className="pt-4">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">{t("topics.title")}</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">{t("topics.subtitle")}</p>
      </div>

      {facets.isLoading && (
        <div className="space-y-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-12 rounded-md" />
          ))}
        </div>
      )}

      <div className="space-y-8">
        {topics.length > 0 && (
          <section aria-labelledby="browse-topics">
            <SectionHead
              title={t("topics.section.topics")}
              expanded={!!open.topics}
              onToggle={() => toggle("topics")}
              canExpand={topics.length > SHOW}
            />
            <ul>
              {slice(topics, "topics").map((topic) => (
                <BrowseRow
                  key={topic}
                  href={`/stories?topic=${encodeURIComponent(topic)}`}
                  label={topic}
                  action={<FollowButton topic={topic} size="button" />}
                />
              ))}
            </ul>
          </section>
        )}

        {places.length > 0 && (
          <section aria-labelledby="browse-places">
            <SectionHead
              title={t("topics.section.places")}
              expanded={!!open.places}
              onToggle={() => toggle("places")}
              canExpand={places.length > SHOW}
            />
            <ul>
              {slice(places, "places").map((place) => (
                <BrowseRow
                  key={place.country}
                  href={`/stories?country=${encodeURIComponent(place.country)}`}
                  label={<CountryBadge code={place.country} />}
                  sublabel={t("countries.stat.articles") + " " + formatCompact(place.articles)}
                  action={<FollowButton place={{ placeId: place.country, level: "country" }} size="button" />}
                />
              ))}
            </ul>
          </section>
        )}

        {publishers.length > 0 && (
          <section aria-labelledby="browse-sources">
            <SectionHead
              title={t("topics.section.sources")}
              expanded={!!open.sources}
              onToggle={() => toggle("sources")}
              canExpand={publishers.length > SHOW}
            />
            <ul>
              {slice(publishers, "sources").map((publisher) => (
                <BrowseRow
                  key={publisher}
                  href={`/publishers/${encodeURIComponent(publisher)}`}
                  mark={<PublisherLogo sizePx={24} className="h-6 w-6" />}
                  label={publisher}
                />
              ))}
            </ul>
          </section>
        )}
      </div>
    </PageContainer>
  );
}
