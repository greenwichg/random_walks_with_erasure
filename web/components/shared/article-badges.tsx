"use client";

import { FileText, MessageSquareQuote, Gauge, Building2 } from "lucide-react";
import type { Article, EmotionShare, Lean, Register } from "@/types/domain";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { EMOTION_META } from "@/lib/metrics";
import { leanBucket, leanLabel } from "@/lib/political";
import { cn } from "@/lib/utils";

/** Political viewpoint pill, coloured by bucket. */
export function LeanBadge({ lean, className }: { lean: Lean; className?: string }) {
  const bucket = leanBucket(lean);
  return (
    <Badge variant={bucket} className={className}>
      {leanLabel(lean)}
    </Badge>
  );
}

/** Publisher with its own house-lean dot. */
export function PublisherBadge({ name, lean }: { name: string; lean?: Lean }) {
  const bucket = lean === undefined ? null : leanBucket(lean);
  const color = bucket ? `hsl(var(--${bucket}))` : "hsl(var(--muted-foreground))";
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
      <Building2 className="h-3.5 w-3.5" />
      {name}
      {bucket && <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />}
    </span>
  );
}

/** Confidence pill (top-2 softmax margin from the backend). */
export function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const level = value >= 0.75 ? "high" : value >= 0.5 ? "medium" : "low";
  const variant = level === "high" ? "positive" : level === "medium" ? "caution" : "secondary";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant={variant as "positive" | "caution" | "secondary"} className="cursor-default">
          <Gauge className="h-3 w-3" /> {pct}%
        </Badge>
      </TooltipTrigger>
      <TooltipContent>Model confidence in this article's lean estimate ({level}).</TooltipContent>
    </Tooltip>
  );
}

/** Dominant-emotion pill. */
export function EmotionBadge({ emotion }: { emotion: EmotionShare }) {
  const key = (Object.keys(emotion) as (keyof EmotionShare)[]).reduce((a, b) => (emotion[a] >= emotion[b] ? a : b));
  const meta = EMOTION_META[key];
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
      style={{ background: `${meta.color}1f`, color: meta.color }}
    >
      {meta.label}
    </span>
  );
}

/** Reporting vs opinion pill. */
export function RegisterBadge({ register }: { register: Register }) {
  const map = {
    reporting: { label: "Reporting", icon: FileText, variant: "positive" as const },
    opinion: { label: "Opinion", icon: MessageSquareQuote, variant: "secondary" as const },
    mixed: { label: "Mixed", icon: FileText, variant: "secondary" as const },
  };
  const m = map[register];
  return (
    <Badge variant={m.variant}>
      <m.icon className="h-3 w-3" /> {m.label}
    </Badge>
  );
}

/** A compact row of an article's key attributes (used on cards). */
export function ArticleAttributes({ article, className }: { article: Article; className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      <LeanBadge lean={article.lean} />
      <RegisterBadge register={article.register} />
      <EmotionBadge emotion={article.emotion} />
      <ConfidenceBadge value={article.confidence} />
    </div>
  );
}
