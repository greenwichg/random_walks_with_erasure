"use client";

import * as React from "react";
import Link from "next/link";
import { FileText, MessageSquareQuote, Gauge } from "lucide-react";
import type { Article, EmotionShare, Lean, LeanBucket, Register } from "@/types/domain";
import { PublisherLogo } from "@/components/shared/publisher-logo";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { EMOTION_META } from "@/lib/metrics";
import { leanBucket, leanLabelKey } from "@/lib/political";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Political viewpoint pill, coloured by bucket. Prefers the engine's `bucket` when given. */
export function LeanBadge({
  lean,
  bucket,
  className,
}: {
  lean?: Lean | null;
  bucket?: LeanBucket | null;
  className?: string;
}) {
  const { t } = useTranslation();
  // Unknown lean (an outlet the registry doesn't know — only reading-history reads): show a neutral
  // "Unknown" badge, never a fabricated Center (L2.2).
  if (lean == null) {
    return (
      <Badge variant="secondary" className={className}>
        {t("lean.unknown")}
      </Badge>
    );
  }
  return (
    <Badge variant={bucket ?? leanBucket(lean)} className={className}>
      {t(leanLabelKey(lean))}
    </Badge>
  );
}

/** Publisher with its own house-lean dot, and an optional logo (falls back to the icon).
 *  No dot when the house lean is unknown (unrated outlet — L2.2): absence, never a guessed hue.
 *  The name links to the Publisher Intelligence profile — every publisher mention in the app is
 *  a doorway to its counted profile, not a dead label. */
export function PublisherBadge({
  name, lean, logo, logoFallbacks, emphasis = false,
}: { name: string; lean?: Lean | null; logo?: string; logoFallbacks?: string[];
     /** Anchor the publisher in foreground ink (Discover's river rows) — the most
      *  identity-bearing token on a dense row shouldn't dissolve into the metadata gray. */
     emphasis?: boolean }) {
  const bucket = lean == null ? null : leanBucket(lean);
  const color = bucket ? `hsl(var(--${bucket}))` : "hsl(var(--muted-foreground))";
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs font-medium",
                        emphasis ? "text-foreground" : "text-muted-foreground")}>
      {/* 14px: a favicon is genuinely adequate here, which is why the too-small rule is
          box-relative rather than a blanket ban on small icons. */}
      <PublisherLogo
        logo={logo}
        fallbacks={logoFallbacks}
        sizePx={14}
        className="h-3.5 w-3.5 rounded-sm"
      />
      <Link
        href={`/publishers/${encodeURIComponent(name)}`}
        onClick={(e) => e.stopPropagation()}
        className="transition-colors hover:text-foreground hover:underline"
      >
        {name}
      </Link>
      {bucket && <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />}
    </span>
  );
}

/** Confidence pill (top-2 softmax margin from the backend). */
export function ConfidenceBadge({ value }: { value: number }) {
  const { t } = useTranslation();
  const pct = Math.round(value * 100);
  const level = value >= 0.75 ? "high" : value >= 0.5 ? "medium" : "low";
  const levelKey =
    level === "high" ? "badge.confidence.high" : level === "medium" ? "badge.confidence.medium" : "badge.confidence.low";
  const variant = level === "high" ? "positive" : level === "medium" ? "caution" : "secondary";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant={variant as "positive" | "caution" | "secondary"} className="cursor-default">
          <Gauge className="h-3 w-3" /> {pct}%
        </Badge>
      </TooltipTrigger>
      <TooltipContent>{t("badge.confidence.tooltip", { level: t(levelKey) })}</TooltipContent>
    </Tooltip>
  );
}

/** Dominant-emotion pill. Prefers the engine's `dominant` key when given. */
export function EmotionBadge({ emotion, dominant }: { emotion: EmotionShare; dominant?: keyof EmotionShare }) {
  const { t } = useTranslation();
  const key =
    dominant ??
    (Object.keys(emotion) as (keyof EmotionShare)[]).reduce((a, b) => (emotion[a] >= emotion[b] ? a : b));
  const meta = EMOTION_META[key];
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
      style={{ background: `${meta.color}1f`, color: meta.color }}
    >
      {t(`emotion.${key}`)}
    </span>
  );
}

/** Reporting vs opinion pill. */
export function RegisterBadge({ register }: { register: Register }) {
  const { t } = useTranslation();
  const map = {
    reporting: { labelKey: "register.reporting", icon: FileText, variant: "positive" as const },
    opinion: { labelKey: "register.opinion", icon: MessageSquareQuote, variant: "secondary" as const },
    mixed: { labelKey: "register.mixed", icon: FileText, variant: "secondary" as const },
  };
  const m = map[register];
  return (
    <Badge variant={m.variant}>
      <m.icon className="h-3 w-3" /> {t(m.labelKey)}
    </Badge>
  );
}

/** A compact row of an article's key attributes (used on cards). Absent signals render
 *  nothing (L2.2) — a badge never shows a defaulted value. */
export function ArticleAttributes({ article, className }: { article: Article; className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      <LeanBadge lean={article.lean} bucket={article.leanBucket} />
      {article.register && <RegisterBadge register={article.register} />}
      {article.emotion && (
        <EmotionBadge emotion={article.emotion} dominant={article.dominantEmotion ?? undefined} />
      )}
      {article.confidence != null && <ConfidenceBadge value={article.confidence} />}
    </div>
  );
}
