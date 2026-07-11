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
import { heroCopyKeys } from "@/lib/hero-copy";
import { useTranslation } from "@/lib/i18n";

/** Metric key → its detail route on the report page. */
const metricHref = (key: string) => `/report#${key}`;

export default function DashboardPage() {
  const { data, isLoading, isError, refetch } = useDashboard();
  const { t } = useTranslation();

  // Headline, status badge, and supporting text all come from ONE band (scoreBand) — so a low score
  // can never pair a "Needs work" badge with a "looking healthy" headline. The supporting text names
  // the reader's strongest metric and their biggest opportunity (the lowest-scoring diet metric;
  // Confidence is a reliability meta-metric, not a lever, so it's excluded).
  const band = scoreBand(data?.overall ?? 0);
  const hero = heroCopyKeys(band.label);
  const byScore = [...(data?.metrics ?? [])].filter((m) => m.key !== "confidence").sort((a, b) => a.score - b.score);
  const lowest = byScore[0];
  const strongest = byScore[byScore.length - 1];
  const heroParams = {
    metric: lowest ? t(`metric.${lowest.key}.label`) : "",
    strong: strongest ? t(`metric.${strongest.key}.label`) : "",
  };

  return (
    <PageContainer>
      <div className="mb-6 flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">{t("dashboard.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("dashboard.subtitle")}</p>
      </div>

      {isLoading && <DashboardSkeleton />}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && (
        <div className="space-y-6">
          {/* Hero: overall score + trend */}
          <div className="grid gap-6 lg:grid-cols-3">
            <Card className="overflow-hidden lg:col-span-2">
              <CardContent className="flex flex-col items-center gap-6 p-6 sm:flex-row sm:p-8">
                <ScoreRing score={data.overall} size={148} label={t("common.of100")} />
                <div className="flex-1 text-center sm:text-left">
                  <div className="flex items-center justify-center gap-2 sm:justify-start">
                    <Badge variant={band.hue}>{t(`band.${band.label}`)}</Badge>
                    <DeltaBadge value={data.overallDelta} suffix={t("report.thisMonth")} />
                  </div>
                  <h2 className="mt-3 text-xl font-semibold tracking-tight">
                    {t(hero.title)}
                  </h2>
                  <p className="mt-1.5 max-w-md text-sm text-muted-foreground">
                    {t(hero.body, heroParams)}
                  </p>
                  <div className="mt-4 flex flex-wrap justify-center gap-2 sm:justify-start">
                    <Button asChild size="sm">
                      <Link href="/report">
                        {t("dashboard.viewReport")} <ArrowRight className="h-4 w-4" />
                      </Link>
                    </Button>
                    <Button asChild size="sm" variant="outline">
                      <Link href="/coach">
                        <Sparkles className="h-4 w-4" /> {t("dashboard.askCoach")}
                      </Link>
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="flex items-center justify-between text-sm font-medium">
                  {t("dashboard.healthTrend")}
                  <span className="text-xs font-normal text-muted-foreground">{t("dashboard.days30")}</span>
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
              <h3 className="text-sm font-semibold text-muted-foreground">{t("dashboard.today")}</h3>
              <Link href="/history" className="text-xs font-medium text-primary hover:underline">
                {t("nav.history")}
              </Link>
            </div>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard icon={BookOpen} label={t("dashboard.articlesRead")} value={`${data.today.articlesRead}`} hue="primary" index={0} />
              <StatCard
                icon={Clock}
                label={data.today.goalMinutes != null ? t("dashboard.minutesRead") : t("dashboard.avgReadingTime")}
                value={`${data.today.goalMinutes != null ? (data.today.minutesRead ?? 0) : data.today.avgReadingMinutes}`}
                sub={
                  data.today.goalMinutes != null
                    ? `${t("dashboard.ofGoal", { min: data.today.goalMinutes })}${data.today.goalMet ? " ✓" : ""}`
                    : t("settings.minutes")
                }
                hue="left"
                index={1}
              />
              <StatCard
                icon={Landmark}
                label={t("dashboard.politicalReading")}
                value={`${Math.round(data.today.politicalShare * 100)}%`}
                hue="center"
                index={2}
              />
              <StatCard icon={Flame} label={t("dashboard.streak")} value={`${data.streakDays}`} sub={t("dashboard.days")} hue="caution" index={3} />
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">{t("dashboard.topTopics")}</span>
              {data.today.topTopics.map((topic) => (
                <TopicChip key={topic} topic={topic} />
              ))}
            </div>
          </div>

          {/* The eight metrics */}
          <div>
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-muted-foreground">{t("dashboard.healthMetrics")}</h3>
              <Link href="/report" className="text-xs font-medium text-primary hover:underline">
                {t("dashboard.fullBreakdown")}
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
