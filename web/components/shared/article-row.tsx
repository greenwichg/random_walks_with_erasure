"use client";

import { motion } from "framer-motion";
import { Check, Clock } from "lucide-react";
import type { Article } from "@/types/domain";
import { LeanBadge, EmotionBadge, RegisterBadge } from "@/components/shared/article-badges";
import { cn } from "@/lib/utils";

/** A compact article line item — used in history, search, and lists. */
export function ArticleRow({
  article,
  meta,
  completed,
  index = 0,
}: {
  article: Article;
  meta?: string;
  completed?: boolean;
  index?: number;
}) {
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
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <LeanBadge lean={article.lean} />
          <RegisterBadge register={article.register} />
          <EmotionBadge emotion={article.emotion} />
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-2">
        <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
          <Clock className="h-3.5 w-3.5" /> {article.readingMinutes}m
        </span>
        {completed !== undefined && (
          <span
            className={cn(
              "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[0.7rem] font-medium",
              completed ? "bg-positive/12 text-positive" : "bg-muted text-muted-foreground",
            )}
          >
            {completed && <Check className="h-3 w-3" />}
            {completed ? "Read" : "Skimmed"}
          </span>
        )}
      </div>
    </motion.div>
  );
}
