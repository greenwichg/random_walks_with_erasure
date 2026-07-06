"use client";

import * as React from "react";
import { BookOpen, Check, ExternalLink } from "lucide-react";
import type { Article } from "@/types/domain";
import { cn } from "@/lib/utils";

/**
 * The single "Read article" control, shared by Recommendations, Discover, and Stories so the Read
 * flow behaves identically everywhere. It records the open (optional `onOpen`) FIRST, then opens the
 * **canonical publisher URL** in a new tab so the browser extension captures the read and the
 * Dashboard / History / Analytics update naturally.
 *
 * It opens the URL ONLY when it is an absolute http(s) URL. A relative/malformed value is never
 * navigated to — that is what made a bad `url` resolve against the app's own origin instead of the
 * publisher. With no usable URL the control still records the open (if there's an `onOpen`), or is
 * disabled — it never offers a broken link.
 */
export function ReadArticleButton({
  article,
  onOpen,
  className,
}: {
  article: Pick<Article, "url">;
  onOpen?: () => void;
  className?: string;
}) {
  const [opened, setOpened] = React.useState(false);
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
          onOpen?.(); // record reception FIRST, inside the click gesture (before window.open)
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
