"use client";

import * as React from "react";
import { BookOpen, Check, ExternalLink } from "lucide-react";
import type { Article } from "@/types/domain";
import { useRecordRead } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { track } from "@/lib/analytics";
import { prefetchContinuation } from "@/lib/continuation";
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
  label,
  className,
}: {
  article: Pick<Article, "url"> & Partial<Pick<Article, "id" | "headline" | "description">>;
  openedFrom?: string;
  onOpen?: () => void;
  /** Context-aware CTA label (Commit 22, recommendations); defaults to the shared "Read article". */
  label?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const [opened, setOpened] = React.useState(false);
  const recordRead = useRecordRead();
  const href = article.url && /^https?:\/\//i.test(article.url) ? article.url : null;
  const actionable = Boolean(href || onOpen);

  return (
    <button
      type="button"
      disabled={!actionable}
      aria-pressed={opened}
      title={href ? t("read.openTitle") : onOpen ? t("read.recordTitle") : t("read.noLinkTitle")}
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
            track("article_read", { source: openedFrom }); // PA1 activation event (best-effort)
            // Story Continuation: ask for the opposing account NOW, so the request overlaps the tab
            // switch and the answer is cached before the reader comes back. Fire-and-forget — never
            // awaited, because the publisher's tab must open at once.
            prefetchContinuation(href);
          }
          onOpen?.();
        }
        if (href) window.open(href, "_blank", "noopener,noreferrer");
      }}
      className={cn(
        "inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded-lg px-3 text-xs font-medium transition-colors",
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
      {opened ? t("read.opened") : href ? label ?? t("read.readArticle") : t("read.noLink")}
    </button>
  );
}
