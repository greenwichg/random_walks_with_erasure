"use client";

import { useAnalytics } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { PageContainer } from "@/components/layout/page-container";
import { SectionCard } from "@/components/shared/section-card";
import { TrendChart } from "@/components/shared/trend-chart";
import { StackedBar } from "@/components/shared/stacked-bar";
import { MultiLineChart } from "@/components/shared/multi-line-chart";
import { ProfileProgress } from "@/components/shared/profile-progress";
import { ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";
import { EMOTION_META } from "@/lib/metrics";

export default function AnalyticsPage() {
  const { data, isLoading, isError, refetch } = useAnalytics();
  const { t } = useTranslation();

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">{t("analytics.title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("analytics.subtitle")}</p>
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-64 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && (
        <div className="space-y-6">
          {/* Estimate vs Measured + coverage — Analytics carries the same context; while the reader is
              still building their profile, the trends below fill in as they read. */}
          <ProfileProgress coverage={data.coverage} />

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <SectionCard title={t("analytics.healthImprovement")} info={t("analytics.healthImprovementInfo")} className="lg:col-span-2">
            {/* Overall Information Health is a normalized 0–100 score: pin the axis to [0,100] so the
                trend reads on the same fixed scale as the diversity metrics below — never auto-scaled. */}
            <TrendChart data={data.healthImprovement} height={240} domain={[0, 100]} />
          </SectionCard>

          <SectionCard title={t("analytics.readingVolume")} info={t("analytics.readingVolumeInfo")}>
            <StackedBar
              data={data.readingOverTime}
              series={[{ key: "overall", label: t("analytics.articles"), color: "hsl(var(--primary))" }]}
              stacked={false}
              height={220}
            />
          </SectionCard>

          <SectionCard title={t("analytics.topicDiversity")} info={t("analytics.topicDiversityInfo")}>
            <TrendChart data={data.topicDiversity} height={220} color="hsl(var(--primary))" domain={[0, 100]} />
          </SectionCard>

          <SectionCard title={t("analytics.politicalDiversity")} info={t("analytics.politicalDiversityInfo")}>
            <TrendChart data={data.politicalDiversity} height={220} color="hsl(var(--center))" domain={[0, 100]} />
          </SectionCard>

          <SectionCard title={t("analytics.publisherDiversity")} info={t("analytics.publisherDiversityInfo")}>
            <TrendChart data={data.publisherDiversity} height={220} color="hsl(var(--left))" domain={[0, 100]} />
          </SectionCard>

          <SectionCard title={t("analytics.emotionalTone")} info={t("analytics.emotionalToneInfo")}>
            {/* Trends of 5 shares: a multi-line chart gives every series a common baseline
                (a 100% stack can't for its middle segments); the shared tooltip keeps the
                composition-at-a-point reading; the legend keeps identity off color-alone. */}
            <MultiLineChart
              data={data.emotion}
              series={(Object.keys(EMOTION_META) as (keyof typeof EMOTION_META)[]).map((k) => ({
                key: k,
                label: t(`emotion.${k}`),
                color: EMOTION_META[k].color,
              }))}
              percent
              height={200}
            />
          </SectionCard>

          <SectionCard title={t("analytics.reportingVsOpinion")} info={t("analytics.reportingVsOpinionInfo")}>
            <StackedBar
              data={data.reporting}
              series={[
                { key: "reporting", label: t("analytics.reporting"), color: "hsl(var(--positive))" },
                { key: "opinion", label: t("analytics.opinion"), color: "hsl(var(--muted-foreground))" },
              ]}
              percent
              height={220}
            />
          </SectionCard>

          <SectionCard title={t("analytics.recAcceptance")} info={t("analytics.recAcceptanceInfo")}>
            {/* ignored uses --muted-foreground, not --muted: the latter is ~the card background
                in BOTH themes (dark: 15% vs 10% lightness), which rendered the ignored bar as an
                invisible ghost. Emphasis pattern: accepted carries the accent, ignored a visible
                de-emphasis gray; the legend keeps the pair off color-alone identity. */}
            <StackedBar
              data={data.recommendationAcceptance}
              series={[
                { key: "accepted", label: t("analytics.accepted"), color: "hsl(var(--positive))" },
                { key: "ignored", label: t("analytics.ignored"), color: "hsl(var(--muted-foreground))" },
              ]}
              stacked={false}
              height={200}
              showLegend
            />
          </SectionCard>
          </div>
        </div>
      )}
    </PageContainer>
  );
}
