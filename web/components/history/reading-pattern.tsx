"use client";

import { CalendarRange, Layers, Clock } from "lucide-react";
import { StatCard } from "@/components/dashboard/stat-card";
import { useTranslation } from "@/lib/i18n";
import type { ReadingPattern } from "@/lib/history-insights";

/**
 * Reading Pattern (Phase 3) — a lightweight behavioural strip: how much you read this week, your
 * average contiguous-session size, and your preferred time of day. Complements the content-focused
 * Information Health strip (what you read) with the temporal side (how you read). Reflects the
 * attribute-filtered set across all days (not narrowed to a selected day).
 */
export function ReadingPatternStrip({ pattern }: { pattern: ReadingPattern }) {
  const { t } = useTranslation();
  const preferred =
    pattern.preferredTime === "morning"
      ? t("history.pattern.morning")
      : pattern.preferredTime === "afternoon"
        ? t("history.pattern.afternoon")
        : pattern.preferredTime === "evening"
          ? t("history.pattern.evening")
          : pattern.preferredTime === "night"
            ? t("history.pattern.night")
            : "—";
  return (
    <section aria-label={t("history.pattern.title")}>
      <h3 className="mb-3 text-sm font-semibold text-muted-foreground">{t("history.pattern.title")}</h3>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard icon={CalendarRange} label={t("history.pattern.thisWeek")} value={`${pattern.articlesThisWeek}`} hue="primary" index={0} />
        <StatCard
          icon={Layers}
          label={t("history.pattern.avgSession")}
          value={pattern.sessionCount ? pattern.avgSessionSize.toFixed(1) : "—"}
          hue="center"
          index={1}
        />
        <StatCard icon={Clock} label={t("history.pattern.preferredTime")} value={preferred} hue="caution" index={2} />
      </div>
    </section>
  );
}
