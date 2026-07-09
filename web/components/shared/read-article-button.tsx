"use client";

import * as React from "react";
import { BookOpen, Check, ExternalLink } from "lucide-react";
import type { Article } from "@/types/domain";
import { useRecordRead } from "@/hooks/use-data";
import { cn } from "@/lib/utils";

/**
 * The single "Read article" control, shared by Recommendations, Discover, Search, Stories, and Saved
 * so the Read flow behaves identically everywhere.
 *
 * It records the read into the canonical `/api/me/reads` pipeline FIRST — this is **in-app read
 * tracking, the primary reading source**; the browser extension is now only an optional enhancement
 * for reads that happen OUTSIDE the app — tags it with `openedFrom`, then opens the **canonical
 * publisher URL** in a new tab so Dashboard / History / Analytics / Health update naturally. When a
 * caller passes `onOpen` (recommendations), that reception signal (RecEvent) is also recorded.
 *
 * It opens the URL ONLY when it is an absolute http(s) URL. A relative/malformed value is never
 * navigated to. With no usable URL the control still records `onOpen` (if given), or is disabled.
 */
export function ReadArticleButton({
  article,
  openedFrom,
  onOpen,
  className,
}: {
  article: Pick<Article, "url"> & Partial<Pick<Article, "id" | "headline" | "description">>;
  openedFrom?: string;
  onOpen?: () => void;
  className?: string;
}) {
  const [opened, setOpened] = React.useState(false);
  const recordRead = useRecordRead();
  const href = article.url && /^https?:\/\//i.test(article.url) ? article.url : null;
  const actionable = Boolean(href || onOpen);

  return (
    <button
      type="button"
      disabled={!actionable}
      aria-pressed={opened}
      title={href ? "Open the article and record it as read" : onOpen ? "Record as read" : "No link available"}
      onClick={() => {
        if (!opened) {
          setOpened(true);
          // Record the in-app read FIRST (canonical pipeline), then the optional rec reception.
          if (href) {
            recordRead.mutate({
              url: href,
              title: article.headline,
              description: article.description,
              openedFrom,
            });
          }
          onOpen?.();
        }
        if (href) window.open(href, "_blank", "noopener,noreferrer");
      }}
      className={cn(
        "inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors",
        opened
          ? "bg-positive/15 text-positive"
          : actionable
            ? "bg-primary text-primary-foreground hover:bg-primary/90"
            : "cursor-not-allowed bg-muted text-muted-foreground",
        className,
      )}
    >
      {opened ? (
        <Check className="h-3.5 w-3.5" />
      ) : href ? (
        <ExternalLink className="h-3.5 w-3.5" />
      ) : (
        <BookOpen className="h-3.5 w-3.5" />
      )}
      {opened ? "Opened" : href ? "Read article" : "No link"}
    </button>
  );
}
