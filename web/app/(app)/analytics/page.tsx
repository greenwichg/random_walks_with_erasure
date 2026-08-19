"use client";

import { useAnalytics } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { PageContainer } from "@/components/layout/page-container";
import { useQuery } from "@tanstack/react-query";
import { services, queryKeys } from "@/services";
import { BarList } from "@/components/shared/bar-list";
import { SectionCard } from "@/components/shared/section-card";
import { TrendChart } from "@/components/shared/trend-chart";
import { StackedBar } from "@/components/shared/stacked-bar";
import { MultiLineChart } from "@/components/shared/multi-line-chart";
import { ProfileProgress } from "@/components/shared/profile-progress";
import { ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";
import { EMOTION_META } from "@/lib/metrics";
import { activeLang } from "@/lib/i18n-core";
import { countryName, languageName } from "@/lib/countries";

export default function AnalyticsPage() {
  const { data, isLoading, isError, refetch } = useAnalytics();
  // Counted geographic facts (auth'd) — Geographic Diversity's inputs, not a score.
  const geography = useQuery({ queryKey: queryKeys.geography, queryFn: services.geography });
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
            {/* Every score card here shares one fixed 0–100 axis, owned by TrendChart itself (see
                lib/chart-axis.ts) — so the four read on the same scale and none can be auto-scaled. */}
            <TrendChart data={data.healthImprovement} height={240} />
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
            <TrendChart data={data.topicDiversity} height={220} color="hsl(var(--primary))" />
          </SectionCard>

          <SectionCard title={t("analytics.politicalDiversity")} info={t("analytics.politicalDiversityInfo")}>
            <TrendChart data={data.politicalDiversity} height={220} color="hsl(var(--center))" />
          </SectionCard>

          <SectionCard title={t("analytics.publisherDiversity")} info={t("analytics.publisherDiversityInfo")}>
            <TrendChart data={data.publisherDiversity} height={220} color="hsl(var(--left))" />
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

          {/* Location Intelligence 1.5 — counted geographic facts about the reader's reads (no
              0–100 score: Geographic Diversity is a future metric; these are its honest inputs).
              Renders only once at least one read is located, so a legacy catalog shows nothing
              rather than an empty chart. */}
          {geography.data && geography.data.located > 0 && (
            <SectionCard title={t("analytics.geo.title")} info={t("analytics.geo.info")} className="lg:col-span-2">
              <div className="grid gap-6 sm:grid-cols-3">
                <div>
                  <p className="mb-2 text-xs font-semibold text-muted-foreground">{t("analytics.geo.countries")}</p>
                  <BarList
                    items={Object.entries(geography.data.countries)
                      .sort((a, b) => b[1] - a[1])
                      .slice(0, 6)
                      .map(([code, n]) => ({ label: countryName(code, activeLang()), value: n / geography.data!.located, count: n }))}
                  />
                </div>
                <div>
                  <p className="mb-2 text-xs font-semibold text-muted-foreground">{t("analytics.geo.languages")}</p>
                  <BarList
                    items={Object.entries(geography.data.languages)
                      .sort((a, b) => b[1] - a[1])
                      .slice(0, 6)
                      .map(([code, n]) => ({ label: languageName(code, activeLang()), value: n / geography.data!.located, count: n }))}
                  />
                </div>
                <div>
                  <p className="mb-2 text-xs font-semibold text-muted-foreground">{t("analytics.geo.scope")}</p>
                  <BarList
                    items={Object.entries(geography.data.scope)
                      .filter(([, n]) => n > 0)
                      .sort((a, b) => b[1] - a[1])
                      .map(([key, n]) => ({
                        label: key === "unknown" ? t("lean.unknown") : t(`local.scope.${key}`),
                        value: n / geography.data!.reads,
                        count: n,
                      }))}
                  />
                </div>
              </div>
            </SectionCard>
          )}
          </div>
        </div>
      )}
    </PageContainer>
  );
}
