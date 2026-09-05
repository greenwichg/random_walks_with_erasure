import * as React from "react";
import { View } from "react-native";

import type { Article, SavedArticle } from "@ih/core/domain/types";

import { DiscoverCard } from "@/components/discover/discover-card";
import { PageTitle, Screen } from "@/components/layout/screen";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSaved } from "@/lib/hooks";
import { navigate } from "@/lib/navigation";
import { useTranslation } from "@/lib/i18n-context";

/** Render a stored save snapshot (a possibly-partial Article) as a full Article for the card. */
function toArticle(s: SavedArticle): Article {
  const a = s.article;
  return {
    ...a,
    id: a.id,
    headline: a.headline ?? "Saved article",
    publisher: a.publisher ?? "Unknown",
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

/** Saved Articles — the persisted "read later" surface. Reuses the SAME DiscoverCard (Read + Save/Unsave). */
export default function SavedScreen() {
  const { data, isLoading, isError, refetch } = useSaved();
  const { t } = useTranslation();
  const saved = data ?? [];

  return (
    <Screen>
      <PageTitle title={t("saved.title")} subtitle={t("saved.subtitle")} />

      {isLoading && (
        <View style={{ gap: 20 }} accessibilityElementsHidden>
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} height={224} />
          ))}
        </View>
      )}
      {isError && <ErrorState onRetry={() => void refetch()} />}

      {data && saved.length === 0 && (
        <EmptyState
          icon="bookmark"
          title={t("saved.empty.title")}
          description={t("saved.empty.body")}
          style={{ marginTop: 16 }}
          action={<Button onPress={() => navigate("/discover")}>{t("saved.empty.cta")}</Button>}
        />
      )}

      <View style={{ gap: 20 }}>
        {saved.map((s) => (
          <DiscoverCard key={s.articleId} article={toArticle(s)} openedFrom="saved" />
        ))}
      </View>
    </Screen>
  );
}
