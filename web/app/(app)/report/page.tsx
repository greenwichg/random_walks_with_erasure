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
import { scoreBand, LEAN_META } from "@/lib/metrics";
import { leanBucket } from "@/lib/political";

export default function ReportPage() {
  const { data: report, isLoading, isError, refetch } = useReport();

  const topicItems: BarItem[] =
    report?.topics.slice(0, 8).map((t) => ({ label: t.topic, value: t.share, count: t.count })) ?? [];

  const sourceItems: BarItem[] =
    report?.sources.map((s) => {
      const bucket = leanBucket(s.lean);
      return {
        label: s.source,
        value: s.share,
        count: s.count,
        color: LEAN_META[bucket].color,
        sublabel: LEAN_META[bucket].label,
      };
    }) ?? [];

  return (
    <PageContainer>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Information Health Report</h1>
          {report && (
            <p className="mt-1 text-sm text-muted-foreground">
              Updated{" "}
              {new Date(report.updatedAt).toLocaleDateString("en", { month: "long", day: "numeric", year: "numeric" })}{" "}
              · based on your last 30 days of reading
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => window.print()}>
            <Download className="h-4 w-4" /> Export
          </Button>
          <Button size="sm" asChild>
            <Link href="/coach">
              <Sparkles className="h-4 w-4" /> Discuss with coach
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
                <ScoreRing score={report.overall} size={150} label="of 100" />
                <div className="mt-4 flex items-center gap-2">
                  <Badge variant={scoreBand(report.overall).hue}>{scoreBand(report.overall).label}</Badge>
                  <DeltaBadge value={report.overallDelta} suffix="this month" />
                </div>
                <p className="mt-3 text-sm text-muted-foreground">
                  A balanced, broadly-sourced diet with room to calm the tone and read more across the aisle.
                </p>
                <div className="mt-4 flex w-full items-center justify-between rounded-lg border bg-muted/40 px-3 py-2 text-sm">
                  <span className="flex items-center gap-2 text-muted-foreground">
                    <Gauge className="h-4 w-4" /> Axis confidence
                  </span>
                  <span className="font-medium tabular-nums">{Math.round(report.axisConfidence * 100)}%</span>
                </div>
              </CardContent>
            </Card>

            <SectionCard
              title="Metric overview"
              info="Your six scored health metrics on one scale. Further from the centre is healthier."
              className="lg:col-span-2"
            >
              <MetricRadar metrics={report.metrics} />
            </SectionCard>
          </div>

          {/* Political distribution + attention */}
          <div className="grid gap-6 lg:grid-cols-2">
            <SectionCard title="Political distribution" info="How your reading splits across the spectrum.">
              <div className="pt-2">
                <SpectrumBar distribution={report.viewpoint} height={16} />
                <p className="mt-4 text-sm text-muted-foreground">
                  You read{" "}
                  <span className="font-medium text-foreground">{Math.round(report.viewpoint.left * 100)}% left</span>,{" "}
                  <span className="font-medium text-foreground">{Math.round(report.viewpoint.center * 100)}% center</span>,
                  and{" "}
                  <span className="font-medium text-foreground">{Math.round(report.viewpoint.right * 100)}% right</span>.
                  You do hear both sides — the tilt is toward the left.
                </p>
              </div>
            </SectionCard>

            <SectionCard title="Attention profile" info="The emotional makeup of what you read. Lower 'charged' is calmer.">
              <AttentionProfile attention={report.attention} />
            </SectionCard>
          </div>

          {/* Reading + source distribution */}
          <div className="grid gap-6 lg:grid-cols-2">
            <SectionCard title="Reading distribution" info="Your most-read topics this month.">
              <BarList items={topicItems} />
            </SectionCard>
            <SectionCard title="Source distribution" info="Your publishers, coloured by political lean.">
              <BarList items={sourceItems} />
            </SectionCard>
          </div>

          {/* Blind spots + improvements */}
          <div className="grid gap-6 lg:grid-cols-2">
            <SectionCard title="Blind spots" info="Topics and viewpoints you under-consume.">
              <BlindSpots items={report.blindSpots} />
            </SectionCard>
            <SectionCard title="Recommendations for improvement" info="The highest-impact changes for your score.">
              <Improvements items={report.improvements} />
            </SectionCard>
          </div>

          {/* Full metric breakdown */}
          <div>
            <div className="mb-3 flex items-center gap-1.5">
              <h2 className="text-sm font-semibold">Detailed breakdown</h2>
              <span className="text-xs text-muted-foreground">· tap a metric to learn what it means</span>
            </div>
            <MetricAccordion metrics={report.metrics} />
          </div>
        </div>
      )}
    </PageContainer>
  );
}
