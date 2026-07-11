"use client";

import { Download, Gauge, Sparkles } from "lucide-react";
import Link from "next/link";
import { useReport } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { ScoreRing } from "@/components/shared/score-ring";
import { DeltaBadge } from "@/components/shared/delta-badge";
import { MetricRadar } from "@/components/shared/metric-radar";
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

  // Data-driven captions (derived from the live report, not hardcoded to any diet).
  const vp = report?.viewpoint;
  const tiltText = !vp
    ? ""
    : Math.abs(vp.left - vp.right) < 0.06
      ? "Your reading is well balanced across the spectrum."
      : (() => {
          const side = vp.left > vp.right ? "left" : "right";
          return Math.min(vp.left, vp.right) >= 0.15
            ? `You do hear both sides — the tilt is toward the ${side}.`
            : `Your reading leans heavily ${side}; the other side is thin.`;
        })();

  // Health band comes from the engine (source of truth); fall back to local thresholds.
  const overallBand = report ? resolveBand(report.overall, report.band) : null;
  const dietSummary = !overallBand
    ? ""
    : overallBand.label === "Healthy"
      ? "A broad, balanced reading diet — keep it up."
      : overallBand.label === "Fair"
        ? "A reasonable diet, with clear room to broaden and balance it."
        : "Your reading is fairly narrow right now — a few changes would help a lot.";

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
          {/* Overall + radar */}
          <div className="grid gap-6 lg:grid-cols-3">
            <Card>
              <CardContent className="flex flex-col items-center p-6 text-center">
                <ScoreRing score={report.overall} size={150} label={t("common.of100")} band={report.band} />
                <div className="mt-4 flex items-center gap-2">
                  {overallBand && <Badge variant={overallBand.hue}>{t(`band.${overallBand.label}`)}</Badge>}
                  <DeltaBadge value={report.overallDelta} suffix={t("report.thisMonth")} />
                </div>
                <p className="mt-3 text-sm text-muted-foreground">{dietSummary}</p>
                <div className="mt-4 flex w-full items-center justify-between rounded-lg border bg-muted/40 px-3 py-2 text-sm">
                  <span className="flex items-center gap-2 text-muted-foreground">
                    <Gauge className="h-4 w-4" /> {t("report.axisConfidence")}
                  </span>
                  <span className="font-medium tabular-nums">{Math.round(report.axisConfidence * 100)}%</span>
                </div>
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
          <div className="grid gap-6 lg:grid-cols-2">
            <SectionCard title={t("report.politicalDist")} info={t("report.politicalDistInfo")}>
              <div className="pt-2">
                <SpectrumBar distribution={report.viewpoint} height={16} />
                <p className="mt-4 text-sm text-muted-foreground">
                  You read{" "}
                  <span className="font-medium text-foreground">{Math.round(report.viewpoint.left * 100)}% left</span>,{" "}
                  <span className="font-medium text-foreground">{Math.round(report.viewpoint.center * 100)}% center</span>,
                  and{" "}
                  <span className="font-medium text-foreground">{Math.round(report.viewpoint.right * 100)}% right</span>.{" "}
                  {tiltText}
                </p>
              </div>
            </SectionCard>

            <SectionCard title={t("report.attentionProfile")} info={t("report.attentionProfileInfo")}>
              <AttentionProfile attention={report.attention} />
            </SectionCard>
          </div>

          {/* Reading + source distribution */}
          <div className="grid gap-6 lg:grid-cols-2">
            <SectionCard title={t("report.readingDist")} info={t("report.readingDistInfo")}>
              <BarList items={topicItems} />
            </SectionCard>
            <SectionCard title={t("report.sourceDist")} info={t("report.sourceDistInfo")}>
              <BarList items={sourceItems} />
            </SectionCard>
          </div>

          {/* Blind spots + improvements */}
          <div className="grid gap-6 lg:grid-cols-2">
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
            <MetricAccordion metrics={report.metrics} />
          </div>
        </div>
      )}
    </PageContainer>
  );
}
