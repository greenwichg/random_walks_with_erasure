"use client";

import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bot } from "lucide-react";
import type { CoachMessage as TMessage, FeedbackAction, Recommendation } from "@ih/core/domain/types";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { LeanBadge } from "@/components/shared/article-badges";
import { RecommendationCard } from "@/components/recommendations/recommendation-card";
import { WeeklyReviewCard } from "@/components/coach/weekly-review-card";
import { citationLabelKey } from "@ih/core/logic/coach-presentation";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** One chat bubble. Assistant messages can carry grounded citations + article suggestions;
 * Coach v2 replies (RWE_COACH_V2) may additionally carry full recommendation cards — rendered
 * with the SAME RecommendationCard as the feed (no parallel card UI). Every v2 field is
 * optional: a v1 message renders exactly as before. */
export function CoachMessageBubble({
  message,
  onCardAction,
  onCardOpen,
}: {
  message: TMessage;
  /** Feedback for an embedded card (same wiring as the recommendations page). */
  onCardAction?: (articleId: string, action: FeedbackAction) => void;
  /** Reception signal when an embedded card is opened. */
  onCardOpen?: (rec: Recommendation) => void;
}) {
  const { t } = useTranslation();
  const isUser = message.role === "user";
  // dismissing an embedded card hides it from THIS bubble only (the ignore signal still fires)
  const [dismissed, setDismissed] = React.useState<Set<string>>(new Set());
  const cards = (message.cards ?? []).filter((c) => !dismissed.has(c.article.id));
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      className={cn("flex gap-3", isUser && "flex-row-reverse")}
    >
      {isUser ? (
        <Avatar className="h-8 w-8">
          <AvatarFallback className="bg-secondary text-secondary-foreground">AR</AvatarFallback>
        </Avatar>
      ) : (
        <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground">
          <Bot className="h-4 w-4" />
        </div>
      )}

      <div className={cn("flex max-w-[85%] flex-col gap-2", isUser && "items-end")}>
        {/* A structured Weekly Review renders as a dashboard card IN PLACE of the paragraph —
            same facts, scannable form; `content` remains the transcript/fallback rendering for
            messages without the attachment. */}
        {message.weeklyReview ? (
          <WeeklyReviewCard review={message.weeklyReview} />
        ) : (
          <div
            className={cn(
              "rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
              isUser
                ? "rounded-tr-sm bg-primary text-primary-foreground"
                : "rounded-tl-sm border bg-card",
            )}
          >
            {message.content}
          </div>
        )}

        {message.citations && message.citations.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {message.citations.map((c, i) => {
              // v1 cites report metrics (catalog label); v2 may cite any engine evidence key,
              // shown as the raw key — honest and greppable, never a broken catalog lookup.
              const labelKey = citationLabelKey(c.metric);
              return (
                <Badge key={`${c.metric}-${i}`} variant="secondary" className="font-normal">
                  {labelKey ? t(labelKey) : c.metric}: <span className="font-semibold">{c.value}</span>
                </Badge>
              );
            })}
          </div>
        )}

        {/* Coach v2: full recommendation cards — the feed's own card component, so Read /
            Save / Why? / feedback all behave identically to the recommendations page. */}
        {cards.length > 0 && (
          <div className="mt-1 w-full space-y-3">
            <AnimatePresence mode="popLayout">
              {cards.map((rec, i) => (
                <RecommendationCard
                  key={rec.article.id}
                  rec={rec}
                  index={i}
                  onAction={(action) => onCardAction?.(rec.article.id, action)}
                  onOpen={() => onCardOpen?.(rec)}
                  onDismiss={() => setDismissed((prev) => new Set(prev).add(rec.article.id))}
                />
              ))}
            </AnimatePresence>
          </div>
        )}

        {/* v1 suggestions (compact rows). v2 mirrors its cards here too — skip the duplicate
            (also when every card was dismissed: a dismissal must not resurface the article). */}
        {!message.cards?.length && message.suggestions && message.suggestions.length > 0 && (
          <div className="mt-1 w-full space-y-2">
            {message.suggestions.map((a) => (
              <div key={a.id} className="rounded-lg border bg-muted/30 p-3">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-medium">{a.publisher}</span>
                  {a.topic && (
                    <>
                      <span>·</span>
                      <span>{a.topic}</span>
                    </>
                  )}
                </div>
                <p className="mt-1 text-sm font-medium leading-snug">{a.headline}</p>
                <div className="mt-2">
                  <LeanBadge lean={a.lean} bucket={a.leanBucket} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </motion.div>
  );
}

/** Animated "thinking" indicator. */
export function CoachTyping() {
  return (
    <div className="flex gap-3">
      <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground">
        <Bot className="h-4 w-4" />
      </div>
      <div className="flex items-center gap-1 rounded-2xl rounded-tl-sm border bg-card px-4 py-3.5">
        {[0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="h-1.5 w-1.5 rounded-full bg-muted-foreground/60"
            animate={{ opacity: [0.3, 1, 0.3], y: [0, -2, 0] }}
            transition={{ duration: 1, repeat: Infinity, delay: i * 0.15 }}
          />
        ))}
      </div>
    </div>
  );
}
