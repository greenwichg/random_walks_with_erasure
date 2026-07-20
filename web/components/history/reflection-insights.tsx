"use client";

import { Scale, Newspaper, Layers, FileText } from "lucide-react";
import { SectionCard } from "@/components/shared/section-card";
import { useTranslation } from "@/lib/i18n";
import type { HistoryInsights } from "@/lib/history-insights";

/**
 * Reflection / Insights (Phase 1) — a compact, plain-language read of the notable patterns in the
 * reads currently in view: political tilt, publisher concentration, topic breadth, reporting mix.
 * The classifiers live in lib/history-insights (softly thresholded, unit-tested); this layer only
 * maps each discriminator to a catalog string, so every key is referenced literally for check:i18n.
 */
export function ReflectionInsights({ insights }: { insights: HistoryInsights }) {
  const { t } = useTranslation();
  const pct = (x: number) => Math.round(x * 100);

  const political =
    insights.politicalTilt === "left"
      ? t("history.insight.leansLeft")
      : insights.politicalTilt === "right"
        ? t("history.insight.leansRight")
        : t("history.insight.balanced");

  const concentration =
    insights.concentration === "concentrated" && insights.mostReadPublisher
      ? t("history.insight.concentrated", { pct: pct(insights.topPublisherShare), publisher: insights.mostReadPublisher })
      : t("history.insight.spread", { n: insights.publisherCount });

  const topics =
    insights.topicBreadth === "narrow"
      ? t("history.insight.topicsNarrow", { n: insights.topicCount })
      : insights.topicBreadth === "broad"
        ? t("history.insight.topicsBroad", { n: insights.topicCount })
        : t("history.insight.topicsModerate", { n: insights.topicCount });

  const reporting =
    insights.reportingTilt === "reporting"
      ? t("history.insight.reportingHeavy", { pct: pct(insights.reportingShare) })
      : insights.reportingTilt === "opinion"
        ? t("history.insight.opinionHeavy", { pct: pct(insights.opinionShare) })
        : t("history.insight.reportingMixed");

  const notes = [
    { Icon: Scale, text: political },
    { Icon: Newspaper, text: concentration },
    { Icon: Layers, text: topics },
    { Icon: FileText, text: reporting },
  ];

  return (
    <SectionCard title={t("history.insightsTitle")}>
      <ul className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
        {notes.map(({ Icon, text }, i) => (
          <li key={i} className="flex items-center gap-2 text-sm text-muted-foreground">
            <Icon className="h-4 w-4 shrink-0 opacity-70" />
            <span>{text}</span>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}
