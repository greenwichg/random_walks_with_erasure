"use client";

import { motion } from "framer-motion";
import { Clock } from "lucide-react";
import type { Article } from "@/types/domain";
import { LeanBadge, EmotionBadge, RegisterBadge } from "@/components/shared/article-badges";
import { ReadArticleButton } from "@/components/shared/read-article-button";
import { useTranslation } from "@/lib/i18n";

/** A compact article line item for the reading-history timeline. */
export function ArticleRow({
  article,
  meta,
  source,
  index = 0,
}: {
  article: Article;
  meta?: string;
  /** `openedFrom` provenance (recommendations / discover / …); rendered subtly when recognised. */
  source?: string;
  index?: number;
}) {
  const { t } = useTranslation();
  const sourceLabel =
    source === "recommendations"
      ? t("history.source.recommendations")
      : source === "discover"
        ? t("history.source.discover")
        : source === "stories"
          ? t("history.source.stories")
          : source === "search"
            ? t("history.source.search")
            : source === "saved"
              ? t("history.source.saved")
              : source === "ai-coach"
                ? t("history.source.aiCoach")
                : null;
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index * 0.03, 0.3) }}
      className="group flex items-start gap-4 rounded-lg border bg-card p-4 transition-colors hover:bg-accent/30"
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <span className="font-medium">{article.publisher}</span>
          <span>·</span>
          <span>{article.topic}</span>
          {meta && (
            <>
              <span>·</span>
              <span>{meta}</span>
            </>
          )}
        </div>
        <h4 className="mt-1 font-medium leading-snug">{article.headline}</h4>
        {sourceLabel && (
          <p className="mt-1 text-[0.7rem] text-muted-foreground">{t("history.openedFrom", { source: sourceLabel })}</p>
        )}
        {/* Lean stays the primary signal; Register + Emotion are kept but visually secondary. */}
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <LeanBadge lean={article.lean} bucket={article.leanBucket} />
          <span className="flex flex-wrap items-center gap-1.5 opacity-70">
            <RegisterBadge register={article.register} />
            <EmotionBadge emotion={article.emotion} dominant={article.dominantEmotion} />
          </span>
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-2">
        {/* Estimated (not measured) reading time — labeled honestly; the value is a deterministic
            per-article estimate, not tracked dwell. */}
        <span
          className="inline-flex items-center gap-1 text-xs text-muted-foreground"
          title={t("read.estMinutesTitle")}
        >
          <Clock className="h-3.5 w-3.5" /> {t("read.estMinutes", { n: article.readingMinutes })}
        </span>
        {/* Same shared Read control as Discover/Stories/Recommendations — opens the canonical
            article.url in a new tab via the /api/me/reads pipeline; disabled when no URL. */}
        <ReadArticleButton article={article} openedFrom="history" />
      </div>
    </motion.div>
  );
}
