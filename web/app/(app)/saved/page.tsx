"use client";

import * as React from "react";
import { Bookmark } from "lucide-react";
import { useSaved } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { DiscoverCard } from "@/components/discover/discover-card";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";
import type { Article, SavedArticle } from "@/types/domain";

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
    publisherLean: a.publisherLean ?? 0,
    topic: a.topic ?? "General",
    lean: a.lean ?? 0,
    leanBucket: a.leanBucket ?? "center",
    confidence: a.confidence ?? 0.7,
    emotion: a.emotion ?? NEUTRAL_EMOTION,
    dominantEmotion: a.dominantEmotion ?? "neutral",
    register: a.register ?? "reporting",
    publishedAt: a.publishedAt ?? s.savedAt ?? "",
    readingMinutes: a.readingMinutes ?? 3,
  } as Article;
}

export default function SavedPage() {
  const { data, isLoading, isError, refetch } = useSaved();
  const saved = data ?? [];

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Saved</h1>
        <p className="mt-1 max-w-xl text-sm text-muted-foreground">
          Articles you saved to read later. Saving isn&apos;t reading — click <strong>Read</strong> to
          open one and record it in your Reading History.
        </p>
      </div>

      {isLoading && (
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-56 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && saved.length === 0 && (
        <EmptyState
          icon={Bookmark}
          title="No saved articles yet"
          description="Tap Save on any article in Recommendations, Discover, Search, or a Story to keep it here for later."
          className="mt-4"
        />
      )}

      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
        {saved.map((s, i) => (
          <DiscoverCard key={s.articleId} article={toArticle(s)} index={i} openedFrom="saved" />
        ))}
      </div>
    </PageContainer>
  );
}
