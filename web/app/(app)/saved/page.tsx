"use client";

import * as React from "react";
import Link from "next/link";
import { Bookmark } from "lucide-react";
import { useSaved } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { PageContainer } from "@/components/layout/page-container";
import { DiscoverCard } from "@/components/discover/discover-card";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { Article, SavedArticle } from "@ih/core/domain/types";

// Saved Articles — the persisted "read later" surface (Commit 14). Reuses the SAME DiscoverCard
// (Read + Save/Unsave) as Discover/Search, so there is one card and one Save/Read implementation.
// Saving is NOT reading: opening a saved article via Read records it into Reading History
// (openedFrom="saved"); the Save button here toggles to unsave.

const NEUTRAL_EMOTION = { fear: 0, outrage: 0, analysis: 0, positive: 0, neutral: 1 };

/** Render a stored save snapshot (a possibly-partial Article, e.g. from Story coverage) as a full
 *  Article for the card, filling sensible defaults for any field the snapshot didn't carry. */
function toArticle(s: SavedArticle): Article {
  const a = s.article;
  return {
    ...a,
    id: a.id,
    headline: a.headline ?? "Saved article",
    publisher: a.publisher ?? "Unknown",
    // Unknown lean STAYS unknown (L2.2) — a snapshot missing it renders "Unknown", never Center.
    publisherLean: a.publisherLean ?? null,
    topic: a.topic ?? "",
    lean: a.lean ?? null,
    leanBucket: a.leanBucket ?? null,
    confidence: a.confidence ?? null,
    emotion: a.emotion ?? null,
    dominantEmotion: a.dominantEmotion ?? null,
    register: a.register ?? null,
    publishedAt: a.publishedAt ?? s.savedAt ?? "",
    readingMinutes: a.readingMinutes ?? 3,
  } as Article;
}

export default function SavedPage() {
  const { data, isLoading, isError, refetch } = useSaved();
  const { t } = useTranslation();
  const saved = data ?? [];

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">{t("saved.title")}</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">{t("saved.subtitle")}</p>
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-56 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && saved.length === 0 && (
        <EmptyState
          icon={Bookmark}
          title={t("saved.empty.title")}
          description={t("saved.empty.body")}
          className="mt-4"
          action={
            <Button asChild>
              <Link href="/discover">{t("saved.empty.cta")}</Link>
            </Button>
          }
        />
      )}

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
        {saved.map((s, i) => (
          <DiscoverCard key={s.articleId} article={toArticle(s)} index={i} openedFrom="saved" />
        ))}
      </div>
    </PageContainer>
  );
}
