"use client";

import * as React from "react";
import { Bookmark, BookmarkCheck } from "lucide-react";
import type { SavableArticle } from "@ih/core/domain/types";
import { useSaved, useSaveArticle, useUnsaveArticle } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The single "Save" control, shared by Recommendations, Discover, Search, and Story detail so the
 * save flow behaves identically everywhere (mirrors {@link ReadArticleButton}).
 *
 * Saved state is the persisted **server truth** (from `useSaved`), so it survives a refresh and — once
 * `onSettled` refetches — stays consistent across tabs. Clicking toggles it with an optimistic update
 * and rollback, and the profile's Saved counter moves in the same optimistic step. The article `id`
 * (its canonical URL) is the persistence key.
 */
export function SaveButton({
  article,
  className,
  compact = false,
}: {
  article: SavableArticle;
  className?: string;
  /** Icon-only variant for surfaces where the card itself is the primary affordance (Discover's
   *  front-page tier + river): the labeled pill repeated on every card outweighed the content it
   *  sat under. Same state machine, same titles — the label moves into `aria-label`. */
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const { data: saved } = useSaved();
  const save = useSaveArticle();
  const unsave = useUnsaveArticle();

  const isSaved = (saved ?? []).some((s) => s.articleId === article.id);
  const pending = save.isPending || unsave.isPending;
  const title = isSaved ? t("save.removeTitle") : t("save.saveTitle");

  return (
    <button
      type="button"
      aria-pressed={isSaved}
      aria-label={compact ? title : undefined}
      disabled={pending || !article.id}
      title={title}
      onClick={(e) => {
        // The compact variant lives inside clickable cards/rows — saving must never ALSO open.
        e.stopPropagation();
        (isSaved ? unsave.mutate(article.id) : save.mutate(article));
      }}
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg text-xs font-medium transition-colors",
        compact ? "h-8 w-8 justify-center" : "h-8 px-3",
        isSaved
          ? compact
            ? "text-primary hover:bg-primary/10"
            : "bg-primary/15 text-primary hover:bg-primary/20"
          : compact
            ? "text-muted-foreground hover:bg-muted hover:text-foreground"
            : "bg-muted text-muted-foreground hover:bg-muted/70 hover:text-foreground",
        pending && "opacity-70",
        className,
      )}
    >
      {isSaved ? <BookmarkCheck className="h-3.5 w-3.5" /> : <Bookmark className="h-3.5 w-3.5" />}
      {!compact && (isSaved ? t("save.saved") : t("save.save"))}
    </button>
  );
}
