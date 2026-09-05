import * as React from "react";

import type { Article } from "@ih/core/domain/types";

import { track } from "./analytics.ts";
import { useRecordRead } from "./hooks.ts";
import { openExternal } from "./navigation.ts";

type ReadableArticle = Pick<Article, "url"> & Partial<Pick<Article, "id" | "headline" | "description">>;

/**
 * The Read ACTION — one owner for the sequence, as on the web (`read-article-button.tsx`):
 * record the in-app read FIRST (the canonical `/api/me/reads` pipeline), tag `openedFrom`, fire
 * the activation event, then open the canonical URL. Idempotent per mount — a double tap cannot
 * double-record. Only absolute http(s) URLs are ever opened.
 *
 * The web's `prefetchContinuation` is absent: Story Continuation is a browser return-visit
 * feature (docs/MOBILE_APP_PLAN.md §4).
 */
export function useReadArticleAction(article: ReadableArticle, openedFrom?: string, onOpen?: () => void) {
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
      track("article_read", { source: openedFrom });
    }
    onOpen?.();
    // recordRead is a stable mutation handle; article fields are read at call time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, href, openedFrom, onOpen, article.headline, article.description]);

  const open = React.useCallback(() => {
    record();
    if (href) openExternal(href);
  }, [record, href]);

  return { opened, href, actionable: Boolean(href || onOpen), record, open };
}
