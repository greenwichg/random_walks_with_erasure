"use client";

import * as React from "react";
import { CalendarRange, Minus, Sparkles, Target, TrendingDown, TrendingUp } from "lucide-react";
import type { WeeklyReview, WeeklyTrend } from "@ih/core/domain/types";
import { Badge } from "@/components/ui/badge";
import { trendLabelKey, weeklyInsights, weeklyTrendDelta } from "@ih/core/logic/coach-presentation";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The Weekly Review as a scannable dashboard card — the structured rendering of the SAME facts
 * the coach's prose cites (the server attaches both; prose stays the transcript/fallback form).
 *
 * Hierarchy, top to bottom: header → the week's three headline numbers (reads / outlets / daily
 * goal) → the three score trends as mini metric tiles (current value large, delta as a signed,
 * colored figure — never color alone) → top publishers as a ranked line → stored goals → up to
 * two derived insights (arithmetic over the payload's own numbers, computed in
 * lib/coach-presentation so it is unit-tested and never invents a claim).
 *
 * Every section renders only when its data exists — an unmeasured week shows fewer rows, never
 * placeholder zeros.
 */
export function WeeklyReviewCard({ review }: { review: WeeklyReview }) {
  const { t, formatCompact } = useTranslation();
  const insights = weeklyInsights(review);
  const stats: { label: string; value: string }[] = [
    ...(review.reads != null ? [{ label: t("coach.weekly.reads"), value: formatCompact(review.reads) }] : []),
    ...(review.outlets != null
      ? [{ label: t("coach.weekly.outlets"), value: formatCompact(review.outlets) }]
      : []),
    ...(review.goalMinutes != null
      ? [{ label: t("coach.weekly.goal"), value: t("coach.weekly.goalValue", { n: review.goalMinutes }) }]
      : []),
  ];

  return (
    <div className="w-full rounded-2xl rounded-tl-sm border bg-card p-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        <CalendarRange className="h-4 w-4 text-primary" aria-hidden />
        <h4 className="text-sm font-semibold tracking-tight">{t("coach.weekly.title")}</h4>
        <Badge variant="secondary" className="ml-auto font-normal">
          {t("coach.weekly.badge")}
        </Badge>
      </div>

      {/* Headline numbers */}
      {stats.length > 0 && (
        <div className="mt-3 grid grid-cols-3 gap-2">
          {stats.map((s) => (
            <div key={s.label} className="rounded-lg bg-muted/40 px-3 py-2">
              <div className="text-lg font-semibold tabular-nums leading-tight">{s.value}</div>
              <div className="text-[0.7rem] text-muted-foreground">{s.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Score trends */}
      {review.trends.length > 0 && (
        <div className="mt-3">
          <div className="text-[0.7rem] font-semibold uppercase tracking-wide text-muted-foreground">
            {t("coach.weekly.trends")}
          </div>
          <div className="mt-1.5 grid gap-2 sm:grid-cols-3">
            {review.trends.map((trend) => (
              <TrendTile key={trend.metric} trend={trend} />
            ))}
          </div>
        </div>
      )}

      {/* Top publishers, ranked */}
      {review.topPublishers.length > 0 && (
        <div className="mt-3">
          <div className="text-[0.7rem] font-semibold uppercase tracking-wide text-muted-foreground">
            {t("coach.weekly.topPublishers")}
          </div>
          <ol className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs">
            {review.topPublishers.map((p, i) => (
              <li key={p.name} className="inline-flex items-center gap-1.5">
                <span aria-hidden className="font-semibold tabular-nums text-muted-foreground/70">
                  {i + 1}
                </span>
                <span className="font-medium">{p.name}</span>
                <span className="tabular-nums text-muted-foreground">×{formatCompact(p.reads)}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Stored goals */}
      <div className="mt-3">
        <div className="flex items-center gap-1.5 text-[0.7rem] font-semibold uppercase tracking-wide text-muted-foreground">
          <Target className="h-3 w-3" aria-hidden />
          {t("coach.weekly.goals")}
        </div>
        {review.storedGoals && review.storedGoals.length > 0 ? (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {review.storedGoals.map((g) => (
              <Badge key={g} variant="outline" className="font-normal">
                {g}
              </Badge>
            ))}
          </div>
        ) : (
          <p className="mt-1 text-xs text-muted-foreground">{t("coach.weekly.goalsEmpty")}</p>
        )}
      </div>

      {/* Derived insights */}
      {insights.length > 0 && (
        <div className="mt-3 border-t pt-2.5">
          <div className="flex items-center gap-1.5 text-[0.7rem] font-semibold uppercase tracking-wide text-muted-foreground">
            <Sparkles className="h-3 w-3" aria-hidden />
            {t("coach.weekly.insights")}
          </div>
          <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
            {insights.map((ins, i) => (
              <li key={i}>
                {ins.kind === "slip" &&
                  t("coach.weekly.insight.slip", {
                    metric: metricName(t, ins.metric),
                    n: Math.abs(ins.delta),
                  })}
                {ins.kind === "gain" &&
                  t("coach.weekly.insight.gain", {
                    metric: metricName(t, ins.metric),
                    n: ins.delta,
                  })}
                {ins.kind === "steady" && t("coach.weekly.insight.steady")}
                {ins.kind === "concentration" &&
                  t("coach.weekly.insight.concentration", { share: ins.share, publisher: ins.publisher })}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function metricName(t: (k: string, p?: Record<string, string | number>) => string, metric: string) {
  const key = trendLabelKey(metric);
  return key ? t(key) : metric;
}

/** One score trend: current value large, the week's signed delta beside it (colored AND signed —
 *  never color alone), snapshot count as the caption. Unmeasured ends render an em dash. */
function TrendTile({ trend }: { trend: WeeklyTrend }) {
  const { t } = useTranslation();
  const delta = weeklyTrendDelta(trend);
  const DeltaIcon = delta == null || delta === 0 ? Minus : delta > 0 ? TrendingUp : TrendingDown;
  return (
    <div className="rounded-lg border bg-muted/20 px-3 py-2">
      <div className="text-[0.7rem] text-muted-foreground">{metricName(t, trend.metric)}</div>
      <div className="mt-0.5 flex items-baseline gap-1.5">
        <span className="text-lg font-semibold tabular-nums leading-tight">{trend.last ?? "—"}</span>
        <span
          className={cn(
            "inline-flex items-center gap-0.5 text-xs font-medium tabular-nums",
            delta == null || delta === 0
              ? "text-muted-foreground"
              : delta > 0
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-red-600 dark:text-red-400",
          )}
        >
          <DeltaIcon className="h-3 w-3" aria-hidden />
          {delta == null ? "—" : delta === 0 ? "±0" : delta > 0 ? `+${delta}` : `${delta}`}
        </span>
      </div>
      {trend.points != null && (
        <div className="mt-0.5 text-[0.65rem] text-muted-foreground/80">
          {t("coach.weekly.snapshots", { n: trend.points })}
        </div>
      )}
    </div>
  );
}
