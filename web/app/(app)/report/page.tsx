"use client";

import { useEffect } from "react";
import { Download, Gauge, Sparkles } from "lucide-react";
import Link from "next/link";
import { useReport } from "@/hooks/use-data";
import { track } from "@/lib/analytics";
import { PageContainer } from "@/components/layout/page-container";
import { ScoreRing } from "@/components/shared/score-ring";
import { DeltaBadge } from "@/components/shared/delta-badge";
import { MetricRadar } from "@/components/shared/metric-radar";
import { ProfileProgress } from "@/components/shared/profile-progress";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { BarList, type BarItem } from "@/components/shared/bar-list";
import { SectionCard } from "@/components/shared/section-card";
import { ErrorState } from "@/components/shared/states";
import { AttentionProfile } from "@/components/report/attention-profile";
import { MetricAccordion } from "@/components/report/metric-accordion";
import { BlindSpots, Improvements } from "@/components/report/report-widgets";
import { ReportSkeleton } from "@/components/report/report-skeleton";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { resolveBand, LEAN_META } from "@/lib/metrics";
import { useTranslation } from "@/lib/i18n";
import { leanBucket } from "@/lib/political";

export default function ReportPage() {
  const { data: report, isLoading, isError, refetch } = useReport();
  const { t, formatDate } = useTranslation();

  // PA1: the reader saw their Information Health Report — the funnel's "Health Report Generated"
  // stage (and "Measured Report" when mode=measured). Best-effort; fires once per report load.
  useEffect(() => {
    if (report) track("health_report_viewed", { mode: report.mode, coverage: report.coverage });
  }, [report]);

  // axisConfidence is a MEASURED-only field — narrow on `mode` before reading it so an Estimate report
  // (a signed-in reader below the read threshold) never renders "NaN%".
  const confidence = report && report.mode === "measured" ? report.axisConfidence : null;

  const topicItems: BarItem[] =
    report?.topics.slice(0, 8).map((tp) => ({ label: tp.topic, value: tp.share, count: tp.count })) ?? [];

  const sourceItems: BarItem[] =
    report?.sources.map((s) => {
      const bucket = leanBucket(s.lean);
      return {
        label: s.source,
        value: s.share,
        count: s.count,
        color: LEAN_META[bucket].color,
        sublabel: t(`filter.${bucket}`),
      };
    }) ?? [];

  // Data-driven captions (derived from the live report, not hardcoded to any diet) — localized.
  const vp = report?.viewpoint;
  // Per-metric Measurement metadata (ADR-001): coverage + provenance now ride on the metric itself.
  // Viewpoint's coverage and Emotion's coverage each explain how much of the reader's reading the
  // dimension reflects. Present only on a measured report; absent otherwise.
  const measurementFor = (key: string) =>
    report?.metrics.find((m) => m.key === key)?.measurement;
  const vpMeasurement = measurementFor("viewpointBalance");
  const vpUnknown = vpMeasurement
    ? vpMeasurement.coverage.eligible - vpMeasurement.coverage.observed
    : 0;
  const emoMeasurement = measurementFor("emotionalBalance");
  const emoUnknown = emoMeasurement
    ? emoMeasurement.coverage.eligible - emoMeasurement.coverage.observed
    : 0;
  const tiltText = !vp
    ? ""
    : Math.abs(vp.left - vp.right) < 0.06
      ? t("report.tilt.balanced")
      : (() => {
          const side = vp.left > vp.right ? "left" : "right";
          return Math.min(vp.left, vp.right) >= 0.15
            ? t("report.tilt.bothSides", { side: t(`filter.${side}`) })
            : t("report.tilt.leansHeavily", { side: t(`filter.${side}`) });
        })();

  // Health band comes from the engine (source of truth); fall back to local thresholds.
  const overallBand = report ? resolveBand(report.overall, report.band) : null;
  const dietSummary = !overallBand
    ? ""
    : overallBand.label === "Healthy"
      ? t("report.diet.healthy")
      : overallBand.label === "Fair"
        ? t("report.diet.fair")
        : t("report.diet.needsWork");

  return (
    <PageContainer>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("report.title")}</h1>
          {report && (
            <p className="mt-1 text-sm text-muted-foreground">
              {t("report.updatedCaption", {
                date: formatDate(report.updatedAt, { month: "long", day: "numeric", year: "numeric" }),
              })}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => window.print()}>
            <Download className="h-4 w-4" /> {t("report.export")}
          </Button>
          <Button size="sm" asChild>
            <Link href="/coach">
              <Sparkles className="h-4 w-4" /> {t("report.discussCoach")}
            </Link>
          </Button>
        </div>
      </div>

      {isLoading && <ReportSkeleton />}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {report && (
        <div className="space-y-6">
          {/* Estimate vs Measured + coverage — the report header now states whether this is an
              Estimate and how many reads unlock the Measured profile, so the onboarding context
              persists here too. */}
          <ProfileProgress mode={report.mode} coverage={report.coverage} />

          {/* Overall + radar */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <Card>
              <CardContent className="flex flex-col items-center p-6 text-center">
                <ScoreRing score={report.overall} size={150} label={t("common.of100")} band={report.band} />
                <div className="mt-4 flex items-center gap-2">
                  {overallBand && <Badge variant={overallBand.hue}>{t(`band.${overallBand.label}`)}</Badge>}
                  <DeltaBadge value={report.overallDelta} suffix={t("report.thisMonth")} />
                </div>
                <p className="mt-3 text-sm text-muted-foreground">{dietSummary}</p>
                {/* Confidence is measured-only — guarded so an Estimate never renders NaN%. */}
                {confidence != null && (
                  <div className="mt-4 flex w-full items-center justify-between rounded-lg border bg-muted/40 px-3 py-2 text-sm">
                    <span className="flex items-center gap-2 text-muted-foreground">
                      <Gauge className="h-4 w-4" /> {t("report.axisConfidence")}
                    </span>
                    <span className="font-medium tabular-nums">{Math.round(confidence * 100)}%</span>
                  </div>
                )}
              </CardContent>
            </Card>

            <SectionCard
              title={t("report.metricOverview")}
              info={t("report.metricOverviewInfo")}
              className="lg:col-span-2"
            >
              <MetricRadar metrics={report.metrics} />
            </SectionCard>
          </div>

          {/* Political distribution + attention */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <SectionCard title={t("report.politicalDist")} info={t("report.politicalDistInfo")}>
              <div className="pt-2">
                <SpectrumBar distribution={report.viewpoint} height={16} />
                <p className="mt-4 text-sm text-muted-foreground">
                  {t("report.viewpointCaption", {
                    left: Math.round(report.viewpoint.left * 100),
                    center: Math.round(report.viewpoint.center * 100),
                    right: Math.round(report.viewpoint.right * 100),
                  })}{" "}
                  {tiltText}
                </p>
                {vpMeasurement ? (
                  <p className="mt-2 text-xs text-muted-foreground">
                    {t("report.viewpointCoverage", {
                      authoritative: vpMeasurement.coverage.observed,
                      eligible: vpMeasurement.coverage.eligible,
                    })}
                    {vpUnknown > 0 ? (
                      <>
                        {" "}
                        {t("report.viewpointCoverageUnknown", {
                          unknown: vpUnknown,
                        })}
                      </>
                    ) : null}
                  </p>
                ) : null}
              </div>
            </SectionCard>

            <SectionCard title={t("report.attentionProfile")} info={t("report.attentionProfileInfo")}>
              <AttentionProfile attention={report.attention} />
              {emoMeasurement && emoUnknown > 0 ? (
                <p className="mt-4 text-xs text-muted-foreground">
                  {t("report.emotionCoverage", {
                    observed: emoMeasurement.coverage.observed,
                    eligible: emoMeasurement.coverage.eligible,
                  })}{" "}
                  {t("report.emotionCoverageUnknown", { unknown: emoUnknown })}
                </p>
              ) : null}
            </SectionCard>
          </div>

          {/* Reading + source distribution */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <SectionCard title={t("report.readingDist")} info={t("report.readingDistInfo")}>
              <BarList items={topicItems} />
            </SectionCard>
            <SectionCard title={t("report.sourceDist")} info={t("report.sourceDistInfo")}>
              <BarList items={sourceItems} />
            </SectionCard>
          </div>

          {/* Blind spots + improvements */}
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <SectionCard title={t("report.blindSpotsTitle")} info={t("report.blindSpotsInfo")}>
              <BlindSpots items={report.blindSpots} />
            </SectionCard>
            <SectionCard title={t("report.improvementsTitle")} info={t("report.improvementsInfo")}>
              <Improvements items={report.improvements} />
            </SectionCard>
          </div>

          {/* Full metric breakdown */}
          <div>
            <div className="mb-3 flex items-center gap-1.5">
              <h2 className="text-sm font-semibold">{t("report.detailedBreakdown")}</h2>
              <span className="text-xs text-muted-foreground">{t("report.tapMetric")}</span>
            </div>
            <MetricAccordion metrics={report.metrics} coverage={report.coverage} />
          </div>
        </div>
      )}
    </PageContainer>
  );
}
