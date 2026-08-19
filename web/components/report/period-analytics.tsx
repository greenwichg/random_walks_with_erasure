"use client";

import Link from "next/link";
import { BookOpen } from "lucide-react";
import { useAnalytics } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { PageContainer } from "@/components/layout/page-container";
import { SectionCard } from "@/components/shared/section-card";
import { TrendChart } from "@/components/shared/trend-chart";
import { StackedBar } from "@/components/shared/stacked-bar";
import { MultiLineChart } from "@/components/shared/multi-line-chart";
import { ScoreRing } from "@/components/shared/score-ring";
import { DeltaBadge } from "@/components/shared/delta-badge";
import { ProfileProgress } from "@/components/shared/profile-progress";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EMOTION_META } from "@/lib/metrics";

/**
 * The landing page for the two report notifications — "Weekly report ready" and "Monthly deep dive
 * ready". Both used to send the reader to `/report`, which is the CURRENT full health report and
 * says nothing about the period the notification is announcing; a reader who clicked "your weekly
 * report is ready" got the same page whichever notification they clicked, and the same page they
 * would have reached from the sidebar.
 *
 * This is the same analytics data (`useAnalytics` — the same query key, so opening it after
 * /analytics costs no extra request) and the same chart components, WINDOWED to the period the
 * notification is about. Nothing new is computed: the score shown is the last health point inside
 * the window and the delta is against the first one, both already in the series.
 */
export type ReportPeriod = "weekly" | "monthly";

/** How far back each period looks. Weekly mirrors the Monday cadence; monthly the 30-day view
 *  `/analytics` already describes in its own subtitle. */
const WINDOW_DAYS: Record<ReportPeriod, number> = { weekly: 7, monthly: 30 };

/**
 * Rows of a dated series that fall inside the window.
 *
 * A row whose date does not parse is DROPPED rather than kept: this view's entire claim is "here is
 * the period", and a point that cannot be placed in time cannot be shown as belonging to it.
 */
function withinWindow<T extends { date: string }>(rows: T[] | undefined, days: number): T[] {
  if (!rows?.length) return [];
  const cutoff = Date.now() - days * 86_400_000;
  return rows.filter((r) => {
    const at = new Date(r.date).getTime();
    return Number.isFinite(at) && at >= cutoff;
  });
}

export function PeriodAnalytics({ period }: { period: ReportPeriod }) {
  const { data, isLoading, isError, refetch } = useAnalytics();
  const { t } = useTranslation();
  const days = WINDOW_DAYS[period];

  const health = withinWindow(data?.healthImprovement, days);
  const reading = withinWindow(data?.readingOverTime, days);
  const topic = withinWindow(data?.topicDiversity, days);
  const political = withinWindow(data?.politicalDiversity, days);
  const publisher = withinWindow(data?.publisherDiversity, days);
  const emotion = withinWindow(data?.emotion, days);

  // The period's headline: the last score recorded inside it, and how far it moved from the first.
  // Both read straight off the windowed series — a single in-window point means no movement to
  // report, so no delta is rendered rather than a fabricated 0.
  const first = health.at(0);
  const latest = health.at(-1);
  const delta = first && latest && health.length > 1
    ? Number(latest.overall) - Number(first.overall)
    : null;

  // "Nothing in this window" is a real answer, and a different one from "no data at all". Charts
  // with empty series render as blank axes, which reads as broken rather than as quiet.
  const empty =
    !health.length && !reading.length && !topic.length && !political.length &&
    !publisher.length && !emotion.length;

  return (
    <PageContainer>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t(`report.period.${period}.title`)}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t(`report.period.${period}.subtitle`)}</p>
        </div>
        <Button variant="outline" size="sm" asChild>
          <Link href="/report">{t("report.period.viewFull")}</Link>
        </Button>
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-64 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && empty && (
        <EmptyState
          icon={BookOpen}
          title={t("report.period.empty.title")}
          description={t("report.period.empty.description")}
          action={
            <Button asChild>
              <Link href="/discover">{t("report.period.empty.cta")}</Link>
            </Button>
          }
        />
      )}

      {data && !empty && (
        <div className="space-y-6">
          <ProfileProgress coverage={data.coverage} />

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            {latest && (
              <Card>
                <CardContent className="flex flex-col items-center p-6 text-center">
                  <ScoreRing score={Number(latest.overall)} size={150} label={t("common.of100")} />
                  {delta !== null && (
                    <div className="mt-4">
                      <DeltaBadge value={delta} suffix={t(`report.period.${period}.suffix`)} />
                    </div>
                  )}
                </CardContent>
              </Card>
            )}

            <SectionCard
              title={t("analytics.healthImprovement")}
              info={t("analytics.healthImprovementInfo")}
              className={latest ? "lg:col-span-2" : "lg:col-span-3"}
            >
              {/* Pinned to [0,100] for the same reason /analytics pins it: Information Health is a
                  normalized score and must never be auto-scaled into looking dramatic. */}
              <TrendChart data={health} height={240} />
            </SectionCard>
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <SectionCard title={t("analytics.readingVolume")} info={t("analytics.readingVolumeInfo")}>
              <StackedBar
                data={reading}
                series={[{ key: "overall", label: t("analytics.articles"), color: "hsl(var(--primary))" }]}
                stacked={false}
                height={220}
              />
            </SectionCard>

            <SectionCard title={t("analytics.topicDiversity")} info={t("analytics.topicDiversityInfo")}>
              <TrendChart data={topic} height={220} color="hsl(var(--primary))" />
            </SectionCard>

            <SectionCard title={t("analytics.politicalDiversity")} info={t("analytics.politicalDiversityInfo")}>
              <TrendChart data={political} height={220} color="hsl(var(--center))" />
            </SectionCard>

            <SectionCard title={t("analytics.publisherDiversity")} info={t("analytics.publisherDiversityInfo")}>
              <TrendChart data={publisher} height={220} color="hsl(var(--left))" />
            </SectionCard>

            <SectionCard
              title={t("analytics.emotionalTone")}
              info={t("analytics.emotionalToneInfo")}
              className="lg:col-span-2"
            >
              <MultiLineChart
                data={emotion}
                series={(Object.keys(EMOTION_META) as (keyof typeof EMOTION_META)[]).map((k) => ({
                  key: k,
                  label: t(`emotion.${k}`),
                  color: EMOTION_META[k].color,
                }))}
                percent
                height={200}
              />
            </SectionCard>
          </div>
        </div>
      )}
    </PageContainer>
  );
}
