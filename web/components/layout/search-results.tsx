"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, CornerDownLeft, FileText } from "lucide-react";
import { LeanBadge } from "@/components/shared/article-badges";
import { useSearch } from "@/hooks/use-data";
import type { Article } from "@ih/core/domain/types";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * Search, minus the shell it is shown in.
 *
 * There are two shells and one search: the overlay a phone opens (search-command.tsx) and the field
 * the desktop header expands in place (header-search.tsx). Everything that decides what search MEANS
 * — the query, the endpoint, when a query is worth sending, what a result opens, where "see all"
 * goes — lives here, so the two can never drift into two behaviours. What each shell keeps is only
 * its own box: a sheet sized to the visible viewport, or a panel hanging under the header.
 */
export function useSearchLauncher(q: string, close: () => void) {
  const router = useRouter();
  const trimmed = q.trim();
  // Two characters, unchanged: one letter matches most of the catalog and is not a search.
  const active = trimmed.length > 1;
  const { data, isFetching } = useSearch({ query: trimmed, limit: 7 }, active);

  // Closing in order to NAVIGATE must not also pop the overlay's history entry — that would undo
  // the navigation just started. Only the sheet pushes such an entry, but the flag is set on the
  // paths that navigate, so it belongs with them.
  const navigatingRef = React.useRef(false);

  const seeAll = React.useCallback(() => {
    navigatingRef.current = true;
    close();
    router.push(`/search?query=${encodeURIComponent(trimmed)}`);
  }, [close, router, trimmed]);

  // A result opens the canonical publisher URL (the Read flow the extension captures); with no
  // usable link, fall back to the full search page.
  const openArticle = React.useCallback(
    (a: Article) => {
      const href = a.url && /^https?:\/\//i.test(a.url) ? a.url : null;
      // Only the fallback moves THIS tab; opening the publisher in a new one leaves our history
      // alone, so that path still wants the pushed entry taken back out.
      if (!href) navigatingRef.current = true;
      close();
      if (href) window.open(href, "_blank", "noopener,noreferrer");
      else router.push(`/search?query=${encodeURIComponent(trimmed)}`);
    },
    [close, router, trimmed],
  );

  return {
    active,
    isFetching,
    results: data?.results ?? [],
    total: data?.total,
    seeAll,
    openArticle,
    navigatingRef,
  };
}

/** The list itself: the hint, the empty answer, the rows and "see all". The scroll box is the
 *  shell's — a phone's is bounded by the visible viewport, the header's by a fixed max-height. */
export function SearchResultList({
  q,
  active,
  isFetching,
  results,
  total,
  seeAll,
  openArticle,
}: {
  q: string;
  active: boolean;
  isFetching: boolean;
  results: Article[];
  total?: number;
  seeAll: () => void;
  openArticle: (a: Article) => void;
}) {
  const { t } = useTranslation();
  const trimmed = q.trim();

  return (
    <>
      {!active && <p className="px-3 py-8 text-center text-sm text-muted-foreground">{t("searchCmd.hint")}</p>}
      {active && results.length === 0 && !isFetching && (
        <p className="px-3 py-8 text-center text-sm text-muted-foreground">
          {t("searchCmd.noMatches", { q: trimmed })}
        </p>
      )}

      {results.length > 0 && (
        <div className="mb-1">
          <p className="px-3 py-1.5 text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground/70">
            {t("analytics.articles")}
          </p>
          {results.map((a) => (
            <Row key={a.id} icon={FileText} onClick={() => openArticle(a)}>
              <span className="truncate">{a.headline}</span>
              <LeanBadge lean={a.lean} className="ml-auto shrink-0" />
            </Row>
          ))}
          <Row icon={ArrowRight} onClick={seeAll}>
            <span className="text-muted-foreground">
              {t("searchCmd.seeAll", { n: total ?? results.length, q: trimmed })}
            </span>
          </Row>
        </div>
      )}
    </>
  );
}

function Row({
  icon: Icon,
  onClick,
  children,
}: {
  icon: React.ElementType;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "group flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm transition-colors hover:bg-accent",
      )}
    >
      <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
      {children}
      <CornerDownLeft className="h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
    </button>
  );
}
