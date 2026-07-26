"use client";

import Link from "next/link";
import { MapPin } from "lucide-react";
import { useSearch, useSettings } from "@/hooks/use-data";
import { SectionHeader } from "@/components/shared/section-header";
import { ArticleRow } from "@/components/shared/article-row";
import { useTranslation } from "@/lib/i18n";

/**
 * "From your places" — the home rail's location module (Location Intelligence 1.5).
 *
 * Reads the reader's own places from settings (edition first, then the first followed location)
 * and shows the located catalog's latest coverage for that place — the same search surface every
 * other page uses, so a read recorded here is identical to a read anywhere else.
 *
 * Graceful fallbacks, in order: no place configured → a quiet setup pointer (never an empty
 * shell); a place with no located coverage yet → the honest empty note. The module never guesses
 * a location — GPS/geo-IP are out of scope by design.
 */
export function LocalPulse() {
  const { t } = useTranslation();
  const settings = useSettings();
  const place =
    settings.data?.edition ??
    settings.data?.locations?.find((l) => l.level === "country")?.placeId ??
    null;

  const articles = useSearch({ country: place ?? undefined, sort: "newest", limit: 3 }, place != null);

  return (
    <section aria-labelledby="local-pulse-heading" className="rounded-lg border bg-card p-4">
      <SectionHeader
        id="local-pulse-heading"
        title={place ? t("home.pulse.title", { place }) : t("home.pulse.setupTitle")}
        href="/local"
        actionLabel={t("home.viewAll")}
        className="mb-3"
      />

      {place == null && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t("home.pulse.setupBody")}{" "}
          <Link
            href="/settings"
            className="font-medium text-primary transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            {t("nav.settings")}
          </Link>
        </p>
      )}

      {place != null && articles.data && articles.data.results.length === 0 && (
        <p className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <MapPin className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("local.noArticles.body")}
        </p>
      )}

      {place != null && articles.data && articles.data.results.length > 0 && (
        <div className="space-y-2">
          {articles.data.results.map((a, i) => (
            <ArticleRow key={a.id} article={a} index={i} />
          ))}
        </div>
      )}
    </section>
  );
}
