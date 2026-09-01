"use client";

import * as React from "react";
import { BookOpen, Check, ExternalLink } from "lucide-react";
import type { Article } from "@ih/core/domain/types";
import { useRecordRead } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { track } from "@/lib/analytics";
import { prefetchContinuation } from "@/lib/continuation";
import { cn } from "@/lib/utils";

type ReadableArticle = Pick<Article, "url"> & Partial<Pick<Article, "id" | "headline" | "description">>;

/**
 * The Read ACTION, extracted so a whole card or row can be the affordance without duplicating the
 * flow (Discover's front-page tier + river open on surface click; the button below stays for every
 * other caller). One owner for the sequence — record the in-app read FIRST (canonical
 * `/api/me/reads` pipeline), tag `openedFrom`, fire the activation event, prefetch the Story
 * Continuation answer so it overlaps the tab switch — because a flow copied into a second
 * component is a flow the two copies quietly disagree on.
 *
 * `record` never navigates (for real `<a>` clicks, where the browser owns navigation);
 * `open` records then opens the canonical URL. Both are idempotent per mount — a double click
 * cannot double-record. Only absolute http(s) URLs are ever navigated to.
 */
export function useReadArticleAction(
  article: ReadableArticle,
  openedFrom?: string,
  onOpen?: () => void,
) {
  const [opened, setOpened] = React.useState(false);
  const recordRead = useRecordRead();
  const href = article.url && /^https?:\/\//i.test(article.url) ? article.url : null;

  const record = React.useCallback(() => {
    if (opened) return;
    setOpened(true);
    if (href) {
      recordRead.mutate({
        url: href,
        title: article.headline,
        description: article.description,
        openedFrom,
      });
      track("article_read", { source: openedFrom }); // PA1 activation event (best-effort)
      prefetchContinuation(href);
    }
    onOpen?.();
    // recordRead is a stable mutation handle; article fields are read at call time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, href, openedFrom, onOpen, article.headline, article.description]);

  const open = React.useCallback(() => {
    record();
    if (href) window.open(href, "_blank", "noopener,noreferrer");
  }, [record, href]);

  return { opened, href, actionable: Boolean(href || onOpen), record, open };
}

/**
 * The single "Read article" control, shared by Recommendations, Discover, Search, Stories, and Saved
 * so the Read flow behaves identically everywhere — a thin shell over {@link useReadArticleAction}.
 *
 * It opens the URL ONLY when it is an absolute http(s) URL. A relative/malformed value is never
 * navigated to. With no usable URL the control still records `onOpen` (if given), or is disabled.
 */
export function ReadArticleButton({
  article,
  openedFrom,
  onOpen,
  label,
  variant = "solid",
  className,
}: {
  article: ReadableArticle;
  openedFrom?: string;
  onOpen?: () => void;
  /** Context-aware CTA label (Commit 22, recommendations); defaults to the shared "Read article". */
  label?: string;
  /** `soft` = the quiet tinted treatment for dense list rows, where a solid primary pill on every
   *  row is a wall of CTA. Opened/disabled states are identical in both variants — only the
   *  unopened actionable look changes, so the Read pipeline reads the same everywhere. */
  variant?: "solid" | "soft";
  className?: string;
}) {
  const { t } = useTranslation();
  const { opened, href, actionable, open } = useReadArticleAction(article, openedFrom, onOpen);

  return (
    <button
      type="button"
      disabled={!actionable}
      aria-pressed={opened}
      title={href ? t("read.openTitle") : onOpen ? t("read.recordTitle") : t("read.noLinkTitle")}
      onClick={open}
      className={cn(
        "inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded-lg px-3 text-xs font-medium transition-colors",
        opened
          ? "bg-positive/15 text-positive"
          : actionable
            ? variant === "soft"
              ? "bg-primary/10 text-primary hover:bg-primary/20"
              : "bg-primary text-primary-foreground hover:bg-primary/90"
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
