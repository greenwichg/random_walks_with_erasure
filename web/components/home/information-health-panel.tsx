"use client";

import Link from "next/link";
import { BookOpen, Flame } from "lucide-react";
import type { DashboardSummary } from "@ih/core/domain/types";
import { SectionHeader } from "@/components/shared/section-header";
import { ScoreRing } from "@/components/shared/score-ring";
import { DeltaBadge } from "@/components/shared/delta-badge";
import { TrendChart } from "@/components/shared/trend-chart";
import { ProfileProgress } from "@/components/shared/profile-progress";
import { Badge } from "@/components/ui/badge";
import { resolveBand } from "@ih/core/logic/metrics";
import { heroCopyKeys } from "@ih/core/logic/hero-copy";
import { useTranslation } from "@/lib/i18n";
import type { MetricKey } from "@ih/core/domain/types";

/**
 * The reader's Information Health, as the home page's companion rail.
 *
 * This is what makes Hidden View a news-*intelligence* home rather than an aggregator: the
 * coverage on the left is always read next to the reader's own diet on the right.
 *
 * Migration note — nothing was dropped. The former dashboard's Estimate-vs-Measured banner, score
 * ring, delta, 30-day trend and today's-reading stats all live here; the full eight-metric
 * breakdown was never duplicated, it has always been `/report`, which this panel links to.
 */

/** The three diversity dimensions surfaced here, in reading order. Deliberately a subset: these
 *  are the metrics that describe *breadth of coverage*, which is what this page is about. The
 *  remaining metrics (tone, echo, openness, confidence) live on the full report.
 *
 *  A "geographic diversity" dimension is intentionally absent — the engine models no geography,
 *  so there is no honest number to show. */
const PANEL_METRICS: MetricKey[] = [
  "viewpointBalance",
  "topicDiversity",
  "sourceDiversity",
];

export function InformationHealthPanel({ data }: { data: DashboardSummary }) {
  const { t, formatCompact } = useTranslation();
  const band = resolveBand(data.overall);
  const minutes =
    data.today.goalMinutes != null
      ? (data.today.minutesRead ?? 0)
      : data.today.avgReadingMinutes;

  // The reader-facing verdict, carried over from the previous dashboard home so the headline and
  // the badge can never disagree (both derive from the same band). Measured metrics only: an
  // unavailable metric is not an "opportunity", and Confidence is a reliability meta-metric.
  const byScore = [...data.metrics]
    .filter((m) => m.key !== "confidence" && m.available !== false)
    .sort((a, b) => a.score - b.score);
  const lowest = byScore[0];
  const strongest = byScore[byScore.length - 1];
  const hero = heroCopyKeys(band.label);

  return (
    <section
      aria-labelledby="health-heading"
      className="rounded-lg border bg-card p-4"
    >
      <SectionHeader
        id="health-heading"
        title={t("home.health.title")}
        href="/report"
        actionLabel={t("dashboard.viewReport")}
        className="mb-4"
      />

      {data.mode && data.coverage && (
        <ProfileProgress
          mode={data.mode}
          coverage={data.coverage}
          sample={data.sample}
          className="mb-4"
        />
      )}

      {/* A reader with no reading of their own is served a FALLBACK dashboard — the score, the
          band, the "biggest opportunity" sentence, the three meters and the 30-day trend all
          describe somebody else. `sample` says so, and the chip above already says so, but a
          labelled 58/100 is still a number a new reader will take as theirs.

          "Today's reading" below is NOT suppressed: those zeros are this reader's real zeros, and
          they are the honest version of the same panel. */}
      {data.sample ? (
        <div className="rounded-lg border border-dashed bg-muted/30 p-4 text-center">
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("home.health.firstRun")}
          </p>
          <Link
            href="/discover"
            className="mt-3 inline-flex text-xs font-medium text-primary transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            {t("rec.empty.cta")}
          </Link>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-4">
            {/* No `band` prop: ScoreRing resolves the same band from the score itself (resolveBand),
              and DashboardSummary carries no engine band to override it with. */}
            <ScoreRing
              score={data.overall}
              label={t("common.of100")}
              size={92}
            />
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={band.hue}>{t(`band.${band.label}`)}</Badge>
                <DeltaBadge
                  value={data.overallDelta}
                  suffix={t("report.thisMonth")}
                />
              </div>
              <p className="mt-2 text-sm font-medium leading-snug tracking-tight text-balance">
                {t(hero.title)}
              </p>
            </div>
          </div>

          <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
            {t(hero.body, {
              metric: lowest ? t(`metric.${lowest.key}.label`) : "",
              strong: strongest ? t(`metric.${strongest.key}.label`) : "",
            })}
          </p>

          {/* The three breadth dimensions, as compact meters. */}
          <p className="mb-2.5 mt-5 text-xs font-semibold text-muted-foreground">
            {t("dashboard.healthMetrics")}
          </p>
          <ul className="space-y-3">
            {PANEL_METRICS.map((key) => {
              const metric = data.metrics.find((m) => m.key === key);
              const available = metric != null && metric.available !== false;
              return (
                <li key={key}>
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="truncate text-xs font-medium">
                      {t(`metric.${key}.label`)}
                    </span>
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                      {available ? metric!.score : "—"}
                    </span>
                  </div>
                  <div
                    aria-hidden
                    className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted"
                  >
                    {/* Neutral ink, not the brand hue: three meters in accent purple turned the panel
                      into the loudest thing on the page. */}
                    {available && (
                      <div
                        className="h-full rounded-full bg-foreground/55"
                        style={{
                          width: `${Math.max(2, Math.min(100, metric!.score))}%`,
                        }}
                      />
                    )}
                  </div>
                </li>
              );
            })}
          </ul>

          <div className="mt-5 border-t pt-4">
            <div className="mb-2.5 flex items-baseline justify-between">
              <p className="text-xs font-semibold text-muted-foreground">
                {t("dashboard.healthTrend")}
              </p>
              <p className="text-[0.68rem] text-muted-foreground">
                {t("dashboard.days30")}
              </p>
            </div>
            <TrendChart data={data.trend} height={104} showAxis={false} />
          </div>
        </>
      )}

      {/* Today's reading — preserved verbatim from the previous dashboard home. */}
      <div className="mt-4 border-t pt-4">
        <p className="mb-2.5 text-xs font-semibold text-muted-foreground">
          {t("dashboard.today")}
        </p>
        <div className="grid grid-cols-2 gap-3">
          <TodayStat
            icon={BookOpen}
            label={t("dashboard.articlesRead")}
            value={formatCompact(data.today.articlesRead)}
          />
          <TodayStat
            icon={Flame}
            label={t("dashboard.streak")}
            value={formatCompact(data.streakDays)}
            sub={t("dashboard.days")}
          />
          <TodayStat
            label={
              data.today.goalMinutes != null
                ? t("dashboard.minutesRead")
                : t("dashboard.avgReadingTime")
            }
            value={formatCompact(minutes)}
            sub={
              data.today.goalMinutes != null
                ? t("dashboard.ofGoal", { min: data.today.goalMinutes })
                : t("settings.minutes")
            }
          />
          <TodayStat
            label={t("dashboard.politicalReading")}
            value={`${Math.round(data.today.politicalShare * 100)}%`}
          />
        </div>
      </div>

      <Link
        href="/coach"
        className="mt-4 inline-flex text-xs font-medium text-primary transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        {t("dashboard.askCoach")}
      </Link>
    </section>
  );
}

function TodayStat({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon?: React.ElementType;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div>
      <p className="flex items-center gap-1.5 text-[0.68rem] text-muted-foreground">
        {Icon && <Icon className="h-3 w-3" aria-hidden />}
        {label}
      </p>
      <p className="mt-0.5 text-lg font-semibold tabular-nums tracking-tight">
        {value}
      </p>
      {sub && <p className="text-[0.65rem] text-muted-foreground">{sub}</p>}
    </div>
  );
}
