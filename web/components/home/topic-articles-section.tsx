"use client";

import type { Article } from "@ih/core/domain/types";
import { SectionHeader } from "@/components/shared/section-header";
import { Skeleton } from "@/components/ui/skeleton";
import { TopicArticleCard } from "@/components/home/topic-article-card";
import { useTranslation } from "@/lib/i18n";

/**
 * "Latest in {topic}" — the module that completes a thin category view.
 *
 * Story clusters need corroboration from several outlets, so a quiet topic can hold one or two
 * events on a day when the catalog holds thirty fresh articles about it. This grid shows those
 * articles: the same catalog Discover reads, filtered to the topic, dated within the last three
 * days, not already a member of a story shown above, at most two per outlet, newest first
 * (`freshArticles`). The header says exactly that, so a reader never mistakes a single-outlet
 * article for a multi-source story.
 *
 * Two columns from the small breakpoint, one below it; the cards stretch to a row's tallest
 * sibling so their action rows sit flush.
 */
export function TopicArticlesSection({
  topic,
  articles,
  loading = false,
}: {
  topic: string;
  articles: Article[];
  loading?: boolean;
}) {
  const { t } = useTranslation();
  if (!loading && articles.length === 0) return null;

  return (
    <section aria-labelledby="topic-articles-heading" aria-busy={loading || undefined}>
      <SectionHeader
        id="topic-articles-heading"
        title={t("home.topic.latestTitle", { topic })}
        eyebrow={t("home.topic.latestEyebrow")}
        href="/discover"
        actionLabel={t("home.viewAll")}
      />
      <p className="-mt-2 mb-4 text-xs text-muted-foreground">{t("home.topic.latestBody")}</p>

      {loading ? (
        <div className="grid gap-4 sm:grid-cols-2" aria-hidden>
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-64 w-full rounded-lg" />
          ))}
        </div>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2">
          {articles.map((article) => (
            <li key={article.id} className="min-w-0">
              <TopicArticleCard article={article} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
