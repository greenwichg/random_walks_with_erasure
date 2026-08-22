"use client";

import type { ReactNode } from "react";
import { SectionCard } from "@/components/shared/section-card";
import { TopicChip } from "@/components/shared/topic-chip";
import { DistributionBar, type Segment } from "@/components/history/distribution-bar";
import { LEAN_META, EMOTION_META } from "@ih/core/logic/metrics";
import { useTranslation } from "@/lib/i18n";
import type { HistoryInsights } from "@ih/core/logic/history-insights";

/**
 * Daily Summary (Phase 2) — the reflection panel for a selected day: political and emotional
 * exposure, reporting vs opinion, most-read topic/publisher, average estimated read time, and top
 * topics. Richer, named breakdown that the compact Information Health strip deliberately doesn't show.
 */
export function DailySummary({ insights, dayLabel }: { insights: HistoryInsights; dayLabel: string }) {
  const { t } = useTranslation();

  const leanSegs: Segment[] = (["left", "center", "right"] as const).map((k) => ({
    key: k,
    label: t(`filter.${k}`),
    value: insights.leanShare[k],
    color: LEAN_META[k].color,
  }));
  const emoSegs: Segment[] = insights.emotion.map((e) => ({
    key: e.key,
    label: t(`emotion.${e.key}`),
    value: e.n,
    color: EMOTION_META[e.key].color,
  }));
  const regSegs: Segment[] = [
    { key: "reporting", label: t("analytics.reporting"), value: insights.reportingShare, color: "hsl(var(--positive))" },
    { key: "opinion", label: t("analytics.opinion"), value: insights.opinionShare, color: "hsl(var(--muted-foreground))" },
  ];

  return (
    <SectionCard
      title={dayLabel}
      action={<span className="text-xs text-muted-foreground">{t("history.articlesCount", { n: insights.count })}</span>}
    >
      <div className="grid grid-cols-1 gap-x-8 gap-y-5 sm:grid-cols-2">
        <div className="space-y-4">
          <Exposure label={t("history.daily.political")}>
            <DistributionBar segments={leanSegs} />
          </Exposure>
          {emoSegs.length > 0 && (
            <Exposure label={t("history.daily.emotional")}>
              <DistributionBar segments={emoSegs} />
            </Exposure>
          )}
          <Exposure label={t("history.daily.reporting")}>
            <DistributionBar segments={regSegs} />
          </Exposure>
        </div>

        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2.5">
            <MiniStat label={t("history.daily.mostReadTopic")} value={insights.mostReadTopic ?? "—"} />
            <MiniStat label={t("history.daily.mostReadPublisher")} value={insights.mostReadPublisher ?? "—"} />
            <MiniStat label={t("history.daily.avgReadTime")} value={t("history.daily.avgMinutes", { n: Math.round(insights.avgReadingMinutes) })} />
          </div>
          {insights.topTopics.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-muted-foreground">{t("history.daily.topTopics")}</p>
              <div className="flex flex-wrap gap-1.5">
                {insights.topTopics.map((tp) => (
                  <TopicChip key={tp.name} topic={tp.name} />
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </SectionCard>
  );
}

function Exposure({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <p className="mb-1.5 text-xs font-medium text-muted-foreground">{label}</p>
      {children}
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-muted/30 p-2.5">
      <p className="truncate text-sm font-semibold" title={value}>{value}</p>
      <p className="mt-0.5 text-[0.7rem] text-muted-foreground">{label}</p>
    </div>
  );
}
