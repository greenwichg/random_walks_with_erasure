"use client";

import * as React from "react";
import { Bookmark, BookmarkCheck } from "lucide-react";
import type { SavableArticle } from "@/types/domain";
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
}: {
  article: SavableArticle;
  className?: string;
}) {
  const { t } = useTranslation();
  const { data: saved } = useSaved();
  const save = useSaveArticle();
  const unsave = useUnsaveArticle();

  const isSaved = (saved ?? []).some((s) => s.articleId === article.id);
  const pending = save.isPending || unsave.isPending;

  return (
    <button
      type="button"
      aria-pressed={isSaved}
      disabled={pending || !article.id}
      title={isSaved ? t("save.removeTitle") : t("save.saveTitle")}
      onClick={() => (isSaved ? unsave.mutate(article.id) : save.mutate(article))}
      className={cn(
        "inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors",
        isSaved
          ? "bg-primary/15 text-primary hover:bg-primary/20"
          : "bg-muted text-muted-foreground hover:bg-muted/70 hover:text-foreground",
        pending && "opacity-70",
        className,
      )}
    >
      {isSaved ? <BookmarkCheck className="h-3.5 w-3.5" /> : <Bookmark className="h-3.5 w-3.5" />}
      {isSaved ? t("save.saved") : t("save.save")}
    </button>
  );
}
