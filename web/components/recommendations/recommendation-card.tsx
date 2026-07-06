"use client";

import * as React from "react";
import { motion } from "framer-motion";
import {
  Bookmark,
  BookOpen,
  Check,
  Clock,
  ExternalLink,
  ThumbsUp,
  ThumbsDown,
  X,
  TrendingUp,
  Sparkles,
  Route,
} from "lucide-react";
import type { FeedbackAction, Recommendation } from "@/types/domain";
import { METRICS } from "@/lib/metrics";
import { timeAgo } from "@/lib/utils";
import {
  PublisherBadge,
  LeanBadge,
  RegisterBadge,
  EmotionBadge,
  ConfidenceBadge,
} from "@/components/shared/article-badges";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

const STRATEGY_LABEL: Record<Recommendation["strategy"], string> = {
  "rwe-b": "Bridging",
  "rwe-d": "Discovery",
  adaptive: "For you",
};

/** A single recommendation with full transparency + the five feedback actions. */
export function RecommendationCard({
  rec,
  index = 0,
  onAction,
  onOpen,
  onDismiss,
}: {
  rec: Recommendation;
  index?: number;
  onAction?: (action: FeedbackAction) => void;
  onOpen?: () => void;
  onDismiss?: () => void;
}) {
  const { article } = rec;
  const [saved, setSaved] = React.useState(false);
  const [readLater, setReadLater] = React.useState(false);
  const [opened, setOpened] = React.useState(false);
  const [vote, setVote] = React.useState<"up" | "down" | null>(null);
  const helps = METRICS[rec.helpsMetric];

  const act = (action: FeedbackAction) => onAction?.(action);

  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.97, transition: { duration: 0.2 } }}
      transition={{ delay: index * 0.04, duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="group relative flex flex-col rounded-lg border bg-card p-5 shadow-soft transition-shadow hover:shadow-card"
    >
      {/* top row: strategy + dismiss */}
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Badge variant={rec.crossCutting ? "right" : "default"}>
            {rec.crossCutting ? <Route className="h-3 w-3" /> : <Sparkles className="h-3 w-3" />}
            {STRATEGY_LABEL[rec.strategy]}
          </Badge>
          <span className="text-xs text-muted-foreground">{timeAgo(article.publishedAt)}</span>
        </div>
        <button
          onClick={() => {
            act("ignore");
            onDismiss?.();
          }}
          aria-label="Ignore"
          className="grid h-7 w-7 place-items-center rounded-md text-muted-foreground opacity-0 transition-all hover:bg-muted hover:text-foreground group-hover:opacity-100"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* headline + publisher + topic */}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <PublisherBadge name={article.publisher} lean={article.publisherLean} />
          <span className="text-xs text-muted-foreground">·</span>
          <span className="text-xs font-medium text-muted-foreground">{article.topic}</span>
        </div>
        <h3 className="mt-1.5 text-[1.05rem] font-semibold leading-snug tracking-tight">
          {article.headline}
        </h3>
      </div>

      {/* attributes */}
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <LeanBadge lean={article.lean} bucket={article.leanBucket} />
        <RegisterBadge register={article.register} />
        <EmotionBadge emotion={article.emotion} dominant={article.dominantEmotion} />
        <ConfidenceBadge value={article.confidence} />
      </div>

      {/* why recommended + health impact */}
      <div className="mt-4 rounded-lg border bg-muted/30 p-3">
        <div className="flex items-start gap-2">
          <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <p className="text-sm text-muted-foreground">{rec.reason}</p>
        </div>
        <div className="mt-2.5 flex items-center gap-2 pl-6 text-xs">
          <Badge variant="positive">
            <TrendingUp className="h-3 w-3" /> +{rec.healthImpact} health
          </Badge>
          <span className="inline-flex items-center gap-1 text-muted-foreground">
            <helps.icon className="h-3.5 w-3.5" /> Helps {helps.label}
          </span>
        </div>
      </div>

      {/* actions */}
      <div className="mt-4 flex items-center gap-1">
        {/* Primary: opening a recommended read records the reception signal behind Open-Mindedness
            (the existing /me/recommendations/opened endpoint), and — when the recommendation carries
            a verified publisher URL (the live RSS-backed catalog) — opens the real article in a new
            tab so the browser extension captures the read and Dashboard/History/Analytics/Health
            update naturally. A recommendation without a URL just records the open (never fabricated):
            current behavior is preserved and no broken link is offered. window.open runs in the
            click gesture (before the mutation resolves) so it is not blocked and the open is recorded
            first. */}
        <button
          onClick={() => {
            if (!opened) {
              setOpened(true);
              onOpen?.(); // record the recommendation as opened FIRST (Open-Mindedness reception)
            }
            if (article.url) window.open(article.url, "_blank", "noopener,noreferrer");
          }}
          aria-pressed={opened}
          title={article.url ? "Open the article and record it as read" : "Record as read"}
          className={cn(
            "mr-1 inline-flex h-8 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors",
            opened
              ? "bg-positive/15 text-positive"
              : "bg-primary text-primary-foreground hover:bg-primary/90",
          )}
        >
          {opened ? (
            <Check className="h-3.5 w-3.5" />
          ) : article.url ? (
            <ExternalLink className="h-3.5 w-3.5" />
          ) : (
            <BookOpen className="h-3.5 w-3.5" />
          )}
          {opened ? "Opened" : article.url ? "Read article" : "Read"}
        </button>
        <ActionButton
          label="Save"
          active={saved}
          activeClass="text-primary"
          icon={Bookmark}
          onClick={() => {
            setSaved((v) => !v);
            act("save");
          }}
        />
        <ActionButton
          label="Read later"
          active={readLater}
          activeClass="text-left"
          icon={Clock}
          onClick={() => {
            setReadLater((v) => !v);
            act("read-later");
          }}
        />
        <div className="ml-auto flex items-center gap-1">
          <ActionButton
            label="Like"
            active={vote === "up"}
            activeClass="text-positive"
            icon={ThumbsUp}
            onClick={() => {
              setVote((v) => (v === "up" ? null : "up"));
              act("like");
            }}
          />
          <ActionButton
            label="Dislike"
            active={vote === "down"}
            activeClass="text-negative"
            icon={ThumbsDown}
            onClick={() => {
              act("dislike");
              onDismiss?.();
            }}
          />
        </div>
      </div>
    </motion.article>
  );
}

function ActionButton({
  label,
  icon: Icon,
  active,
  activeClass,
  onClick,
}: {
  label: string;
  icon: React.ElementType;
  active?: boolean;
  activeClass?: string;
  onClick: () => void;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          onClick={onClick}
          aria-label={label}
          aria-pressed={active}
          className={cn(
            "grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted",
            active && activeClass,
          )}
        >
          <Icon className={cn("h-[1.05rem] w-[1.05rem]", active && "fill-current")} />
        </button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}
