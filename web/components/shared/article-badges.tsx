"use client";

import * as React from "react";
import { FileText, MessageSquareQuote, Gauge, Building2 } from "lucide-react";
import type { Article, EmotionShare, Lean, LeanBucket, Register } from "@/types/domain";
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
 *  No dot when the house lean is unknown (unrated outlet — L2.2): absence, never a guessed hue. */
export function PublisherBadge({ name, lean, logo }: { name: string; lean?: Lean | null; logo?: string }) {
  const bucket = lean == null ? null : leanBucket(lean);
  const color = bucket ? `hsl(var(--${bucket}))` : "hsl(var(--muted-foreground))";
  const [logoOk, setLogoOk] = React.useState(true);
  React.useEffect(() => setLogoOk(true), [logo]);
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
      {logo && logoOk ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={logo}
          alt=""
          width={14}
          height={14}
          loading="lazy"
          onError={() => setLogoOk(false)}
          className="h-3.5 w-3.5 rounded-sm object-contain"
        />
      ) : (
        <Building2 className="h-3.5 w-3.5" />
      )}
      {name}
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

/** A compact row of an article's key attributes (used on cards). */
export function ArticleAttributes({ article, className }: { article: Article; className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      <LeanBadge lean={article.lean} bucket={article.leanBucket} />
      <RegisterBadge register={article.register} />
      <EmotionBadge emotion={article.emotion} dominant={article.dominantEmotion} />
      <ConfidenceBadge value={article.confidence} />
    </div>
  );
}
