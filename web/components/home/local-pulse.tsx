"use client";

import Link from "next/link";
import { AlertCircle, MapPin } from "lucide-react";
import { useSearch, useSettings } from "@/hooks/use-data";
import { SectionHeader } from "@/components/shared/section-header";
import { ArticleRow } from "@/components/shared/article-row";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";
import { activeLang } from "@/lib/active-lang";
import { countryName } from "@ih/core/logic/countries";

/**
 * "From your places" — the home rail's location module (Location Intelligence 1.5).
 *
 * Reads the reader's own places from settings (edition first, then the first followed location)
 * and shows the located catalog's latest coverage for that place — the same search surface every
 * other page uses, so a read recorded here is identical to a read anywhere else.
 *
 * Graceful fallbacks, in order: no place configured → a quiet setup pointer (never an empty
 * shell); a place with no located coverage yet → the honest empty note; a request that FAILED →
 * a failure note with a retry, never silence. The module never guesses a location — GPS/geo-IP
 * are out of scope by design.
 *
 * That failure branch is not decoration. Every other state here keys off `articles.data`, so a
 * failed request — `data` undefined — used to fall through all of them and render the header
 * alone: a card indistinguishable from "this place has no coverage". Production hit exactly that
 * on 2026-08-02, when the country search took 5,366 ms against the web tier's 6,000 ms deadline
 * and intermittently 503'd; the card said nothing at all, so a hard timeout read as an empty
 * place and took a full investigation to tell apart from missing data.
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
        title={place ? t("home.pulse.title", { place: countryName(place, activeLang()) }) : t("home.pulse.setupTitle")}
        href={place ? `/stories?country=${encodeURIComponent(place)}` : "/stories"}
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

      {place != null && articles.isError && (
        <div className="flex items-center justify-between gap-2">
          <p className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" aria-hidden />
            {t("states.error.body")}
          </p>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 shrink-0 px-2 text-xs"
            onClick={() => void articles.refetch()}
          >
            {t("common.tryAgain")}
          </Button>
        </div>
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
