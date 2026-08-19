"use client";

import * as React from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  ArrowLeftRight,
  Clock,
  HelpCircle,
  ThumbsUp,
  ThumbsDown,
  X,
  Sparkles,
  Route,
} from "lucide-react";
import type { FeedbackAction, Recommendation } from "@/types/domain";
import { useTranslation } from "@/lib/i18n";
import { presentRecommendation } from "@/lib/rec-presentation";
import { PublisherBadge, LeanBadge } from "@/components/shared/article-badges";
import { ContinuationStrip } from "@/components/shared/continuation-strip";
import { ReadArticleButton } from "@/components/shared/read-article-button";
import { SaveButton } from "@/components/shared/save-button";
import { ArticleImage } from "@/components/shared/article-image";
import { WhyDrawer } from "@/components/recommendations/why-drawer";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

const STRATEGY_LABEL_KEY: Record<Recommendation["strategy"], string> = {
  "rwe-b": "rec.strategy.rwe-b",
  "rwe-d": "rec.strategy.rwe-d",
  adaptive: "rec.strategy.adaptive",
  story: "rec.strategy.story",
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
  const { t, localizeExplanation, timeAgo, formatDate } = useTranslation();
  const [readLater, setReadLater] = React.useState(false);
  const [why, setWhy] = React.useState(false);
  const [vote, setVote] = React.useState<"up" | "down" | null>(null);
  // claim → receipt → proof (Commit 22): pure key selection from the resolver's structured
  // explanation — the module invents nothing, the drawer stays the proof layer.
  const pres = presentRecommendation(rec.explanation);
  // History-backed explanations (story match, new publisher) get a slightly stronger evidence
  // container (22.1) so "this is about MY reading" is recognizable at scan speed. Existing
  // primary token only — no new colors.
  const historyBacked = !!pres.comparison || rec.explanation?.type === "new_publisher";

  const act = (action: FeedbackAction) => onAction?.(action);

  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.97, transition: { duration: 0.2 } }}
      transition={{ delay: index * 0.04, duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      // MB1: min-w-0 lets the card shrink to its grid column on mobile (the `min-width:auto` floor
      // was scrolling the recommendations page sideways by ~49px).
      className="group relative flex min-w-0 flex-col rounded-lg border bg-card p-5 shadow-soft transition-shadow hover:shadow-card"
    >
      {/* top row: strategy + dismiss */}
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Badge variant={rec.crossCutting ? "right" : "default"}>
            {rec.crossCutting ? <Route className="h-3 w-3" /> : <Sparkles className="h-3 w-3" />}
            {t(STRATEGY_LABEL_KEY[rec.strategy])}
          </Badge>
          {timeAgo(article.publishedAt) && (
            <span className="text-xs text-muted-foreground">{timeAgo(article.publishedAt)}</span>
          )}
        </div>
        <button
          onClick={() => {
            act("ignore");
            onDismiss?.();
          }}
          aria-label={t("rec.ignore")}
          // Always visible on touch (no hover); hover-reveal only from `sm` up so the desktop card
          // stays clean but the dismiss is never unreachable on a phone.
          className="touch-target grid h-7 w-7 place-items-center rounded-md text-muted-foreground opacity-100 transition-all hover:bg-muted hover:text-foreground sm:opacity-0 sm:group-hover:opacity-100"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Furniture never fronts a card (imageSuspect — the story-hero suspect tier as data);
          this denser card simply reflows text-first, no reserved slot to void. */}
      {!article.imageSuspect && (
        <ArticleImage src={article.image} alt={article.headline} className="mb-3" />
      )}

      {/* headline + publisher + topic */}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <PublisherBadge name={article.publisher} lean={article.publisherLean} logo={article.publisherLogo} logoFallbacks={article.publisherLogoFallbacks} />
          {article.topic && (
            <>
              <span className="text-xs text-muted-foreground">·</span>
              <span className="text-xs font-medium text-muted-foreground">{article.topic}</span>
            </>
          )}
        </div>
        <h3 className="mt-1.5 text-[1.05rem] font-semibold leading-snug tracking-tight">
          {article.headline}
        </h3>
      </div>

      {/* attributes — lean only on the invitation layer (decision-critical for this product);
          register + emotion moved into the drawer's Article metadata (Commit 22) */}
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {/* classifier confidence moved into the Why? drawer (21a.2): a bare % on a
            recommendation card reads as a recommendation score, which it never was */}
        <LeanBadge lean={article.lean} bucket={article.leanBucket} />
      </div>

      {/* claim → receipt → proof (Commit 22): the evidence block SHOWS the resolver's evidence —
          the two-publisher comparison for story matches, structured receipts elsewhere; the
          claim-free fallback keeps the validated sentence. The drawer stays the proof layer. */}
      <div className={cn("mt-4 rounded-lg border p-3", historyBacked ? "border-primary/25 bg-primary/[0.04]" : "bg-muted/30")}>
        {pres.comparison ? (
          <div>
            <div className="flex items-start gap-2">
              <ArrowLeftRight className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <p className="min-w-0 text-sm font-semibold leading-snug">{t(pres.claimKey ?? "rec.whyThisArticle")}</p>
            </div>
            {/* the receipt: You read {A} ✓ → Compare with / Update from {B} — evidence the
                resolver already proved (story membership, publishers, dates) shown as structure */}
            <div className="mt-2.5 divide-y overflow-hidden rounded-md border bg-background/60">
              <div className="flex items-baseline gap-2 px-2.5 py-1.5 text-xs">
                <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("rec.receipt.youRead")}
                </span>
                <span className="min-w-0 flex-1 truncate text-right font-medium">
                  {pres.comparison.readPublisher}
                </span>
                {pres.comparison.readAt && (
                  <span className="shrink-0 text-muted-foreground">
                    ✓ {formatDate(pres.comparison.readAt, { month: "short", day: "numeric" })}
                  </span>
                )}
              </div>
              <div className="flex items-baseline gap-2 bg-primary/5 px-2.5 py-1.5 text-xs">
                <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-primary">
                  {t(pres.comparison.variant === "follow_up" ? "rec.receipt.updateFrom" : "rec.receipt.compareWith")}
                </span>
                <span className="min-w-0 flex-1 truncate text-right font-semibold">
                  {pres.comparison.recPublisher}
                </span>
                {pres.comparison.variant === "follow_up" &&
                  pres.comparison.hoursAfterRead != null &&
                  pres.comparison.hoursAfterRead > 0 && (
                    <span className="shrink-0 text-muted-foreground">
                      {t("rec.receipt.hoursLater", { n: pres.comparison.hoursAfterRead })}
                    </span>
                  )}
              </div>
            </div>
            {(pres.storyHref || pres.comparison.variant === "following") && (
              <div className="mt-2 flex items-center justify-between gap-2 text-xs">
                <span className="text-muted-foreground">
                  {pres.comparison.variant === "following"
                    ? t("rec.receipt.readsSoFar", { n: pres.comparison.storyReads })
                    : null}
                </span>
                {pres.storyHref && (
                  <Link href={pres.storyHref} className="shrink-0 font-medium text-primary hover:underline">
                    {t("rec.viewStory")} →
                  </Link>
                )}
              </div>
            )}
          </div>
        ) : pres.reader || pres.contribution ? (
          <div className="flex items-start gap-2">
            <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <div className="min-w-0">
              {/* Commit 23: the resolver's structured parts — the reader fact leads, the
                  contribution supports. For the documented contribution-first exception
                  (long_tail has no truthful reader fact) the contribution takes the bold slot. */}
              {pres.reader && (
                <p className="text-sm font-semibold leading-snug">
                  {t(pres.reader.key, pres.reader.params)}
                </p>
              )}
              {pres.contribution && (
                <p className={pres.reader ? "mt-0.5 text-xs text-muted-foreground" : "text-sm font-semibold leading-snug"}>
                  {t(pres.contribution.key, pres.contribution.params)}
                </p>
              )}
            </div>
          </div>
        ) : (
          <div className="flex items-start gap-2">
            <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <div className="min-w-0">
              {/* claim-free fallback (coverage_breadth, balanced-profile bridge, unknown types):
                  the resolver's validated sentence, localized — never an over-claim */}
              <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/80">
                {t("rec.whyThisArticle")}
              </div>
              <p className="mt-0.5 text-sm text-muted-foreground">
                {localizeExplanation(rec.explanation ?? { message: rec.reason })}
              </p>
            </div>
          </div>
        )}
        {/* C6: the generic "Helps {metric}" chip is gone — it was a strategy-derived label, not
            evidence. Every line above is a measured fact about THIS reader's history; estimated
            effects live in the Why? drawer, explicitly labeled as estimates. */}
      </div>

      {/* actions — pills never break mid-word (buttons are nowrap); the row itself wraps so
          long FR/DE CTAs move whole pills to a second line instead of deforming them (22.1) */}
      <div className="mt-4 flex flex-wrap items-center gap-1 gap-y-1.5">
        {/* Primary: opening a recommended read records the reception signal behind Open-Mindedness
            (the existing /me/recommendations/opened endpoint via onOpen) and opens the real article
            so the browser extension captures the read and Dashboard/History/Analytics/Health update
            naturally. The shared control only navigates to an absolute publisher URL — never a
            relative value that would resolve to the app's own origin. */}
        <ReadArticleButton
          article={article}
          openedFrom="recommendations"
          onOpen={onOpen}
          label={pres.ctaKey ? t(pres.ctaKey) : undefined}
          className="mr-1"
        />
        <SaveButton article={article} />
        <ActionButton
          label={t("rec.why")}
          active={why}
          activeClass="text-primary"
          icon={HelpCircle}
          onClick={() => setWhy((v) => !v)}
        />
        <ActionButton
          label={t("rec.readLater")}
          active={readLater}
          activeClass="text-primary"
          icon={Clock}
          onClick={() => {
            setReadLater((v) => !v);
            act("read-later");
          }}
        />
        <div className="ml-auto flex items-center gap-1">
          <ActionButton
            label={t("rec.like")}
            active={vote === "up"}
            activeClass="text-positive"
            icon={ThumbsUp}
            onClick={() => {
              setVote((v) => (v === "up" ? null : "up"));
              act("like");
            }}
          />
          <ActionButton
            label={t("rec.dislike")}
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

      {/* the proof behind the card (21a.2): every line is real recommender evidence */}
      <WhyDrawer rec={rec} open={why} />

      {/* Story Continuation — renders nothing unless the reader opened THIS article, came back
          after a real absence, and the engine found an opposing account (§1.4). */}
      {article.url ? <ContinuationStrip anchorUrl={article.url} /> : null}
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
            "touch-target grid h-8 w-8 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted",
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
