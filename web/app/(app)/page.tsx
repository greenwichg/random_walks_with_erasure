"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { BookOpen, Clock, Flame, Landmark, ArrowRight, Sparkles } from "lucide-react";
import { useDashboard } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { ScoreRing } from "@/components/shared/score-ring";
import { DeltaBadge } from "@/components/shared/delta-badge";
import { TrendChart } from "@/components/shared/trend-chart";
import { MetricCard } from "@/components/shared/metric-card";
import { TopicChip } from "@/components/shared/topic-chip";
import { StatCard } from "@/components/dashboard/stat-card";
import { DashboardSkeleton } from "@/components/dashboard/dashboard-skeleton";
import { ErrorState } from "@/components/shared/states";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { METRIC_ORDER, scoreBand } from "@/lib/metrics";

/** Metric key → its detail route on the report page. */
const metricHref = (key: string) => `/report#${key}`;

export default function DashboardPage() {
  const { data, isLoading, isError, refetch } = useDashboard();

  return (
    <PageContainer>
      <div className="mb-6 flex flex-col gap-1">
        <p className="text-sm text-muted-foreground">Good to see you, Alex</p>
        <h1 className="text-2xl font-semibold tracking-tight">Your Information Health</h1>
      </div>

      {isLoading && <DashboardSkeleton />}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && (
        <div className="space-y-6">
          {/* Hero: overall score + trend */}
          <div className="grid gap-6 lg:grid-cols-3">
            <Card className="overflow-hidden lg:col-span-2">
              <CardContent className="flex flex-col items-center gap-6 p-6 sm:flex-row sm:p-8">
                <ScoreRing score={data.overall} size={148} label="of 100" />
                <div className="flex-1 text-center sm:text-left">
                  <div className="flex items-center justify-center gap-2 sm:justify-start">
                    <Badge variant={scoreBand(data.overall).hue}>{scoreBand(data.overall).label}</Badge>
                    <DeltaBadge value={data.overallDelta} suffix="this month" />
                  </div>
                  <h2 className="mt-3 text-xl font-semibold tracking-tight">
                    Your reading diet is looking healthy.
                  </h2>
                  <p className="mt-1.5 max-w-md text-sm text-muted-foreground">
                    You're up {data.overallDelta} points this month — driven by more cross-cutting reads.
                    Your best lever right now is Emotional Balance.
                  </p>
                  <div className="mt-4 flex flex-wrap justify-center gap-2 sm:justify-start">
                    <Button asChild size="sm">
                      <Link href="/report">
                        View full report <ArrowRight className="h-4 w-4" />
                      </Link>
                    </Button>
                    <Button asChild size="sm" variant="outline">
                      <Link href="/coach">
                        <Sparkles className="h-4 w-4" /> Ask the coach
                      </Link>
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center justify-between text-sm font-medium">
                  Health trend
                  <span className="text-xs font-normal text-muted-foreground">30 days</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="px-2 pb-2">
                <TrendChart data={data.trend} height={196} showAxis={false} />
              </CardContent>
            </Card>
          </div>

          {/* Today's reading */}
          <div>
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-muted-foreground">Today's reading</h3>
              <Link href="/history" className="text-xs font-medium text-primary hover:underline">
                View history
              </Link>
            </div>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard icon={BookOpen} label="Articles read" value={`${data.today.articlesRead}`} hue="primary" index={0} />
              <StatCard
                icon={Clock}
                label="Avg. reading time"
                value={`${data.today.avgReadingMinutes}`}
                sub="min"
                hue="left"
                index={1}
              />
              <StatCard
                icon={Landmark}
                label="Political reading"
                value={`${Math.round(data.today.politicalShare * 100)}%`}
                hue="center"
                index={2}
              />
              <StatCard icon={Flame} label="Reading streak" value={`${data.streakDays}`} sub="days" hue="caution" index={3} />
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">Top topics today:</span>
              {data.today.topTopics.map((t) => (
                <TopicChip key={t} topic={t} />
              ))}
            </div>
          </div>

          {/* The eight metrics */}
          <div>
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-muted-foreground">Your health metrics</h3>
              <Link href="/report" className="text-xs font-medium text-primary hover:underline">
                Full breakdown
              </Link>
            </div>
            <motion.div
              className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"
              initial="hidden"
              animate="show"
            >
              {METRIC_ORDER.map((key, i) => {
                const metric = data.metrics.find((m) => m.key === key);
                return metric ? (
                  <MetricCard key={key} metric={metric} href={metricHref(key)} index={i} />
                ) : null;
              })}
            </motion.div>
          </div>
        </div>
      )}
    </PageContainer>
  );
}
