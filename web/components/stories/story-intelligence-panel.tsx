"use client";

import * as React from "react";
import {
  Activity,
  ArrowRight,
  Bell,
  ChevronDown,
  Clock,
  Flag,
  Gauge,
  Milestone,
  Minus,
  Scale,
  Sparkles,
  TrendingDown,
  TrendingUp,
  UserPlus,
  type LucideIcon,
} from "lucide-react";
import { useStoryIntelligence } from "@/hooks/use-data";
import { condenseTimeline } from "@ih/core/logic/story-timeline";
import { Skeleton } from "@/components/ui/skeleton";
import type { StoryLifecycle, StoryMomentum, StoryTimelineEventType } from "@ih/core/domain/types";
import { LEAN_META } from "@ih/core/logic/metrics";
import { cn } from "@/lib/utils";
import { useTranslation } from "@/lib/i18n";
import { formatDate } from "@ih/core/i18n/core";
import { activeLang } from "@/lib/active-lang";

const LIFECYCLE_STYLE: Record<StoryLifecycle, string> = {
  Breaking: "bg-red-500/12 text-red-600 dark:text-red-400 ring-1 ring-red-500/20",
  Developing: "bg-amber-500/12 text-amber-600 dark:text-amber-400 ring-1 ring-amber-500/20",
  Mature: "bg-muted text-foreground/70 ring-1 ring-border",
  Archived: "bg-muted text-muted-foreground ring-1 ring-border",
};

const MOMENTUM_META: Record<StoryMomentum["state"], { icon: LucideIcon; className: string }> = {
  Growing: { icon: TrendingUp, className: "text-emerald-600 dark:text-emerald-400" },
  Stable: { icon: Minus, className: "text-muted-foreground" },
  Declining: { icon: TrendingDown, className: "text-slate-500 dark:text-slate-400" },
};

const TIMELINE_ICON: Record<StoryTimelineEventType, LucideIcon> = {
  first_report: Flag,
  publisher_join: UserPlus,
  perspective_expansion: Scale,
  milestone: Milestone,
  latest: Clock,
};

const fmtDate = (iso?: string) =>
  iso ? formatDate(iso, activeLang(), { month: "short", day: "numeric" }) : "";

/** Locale time of day ("14:32" / "2:32 PM"). The day itself lives on the day divider above. */
const fmtTime = (iso?: string) => {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleTimeString(activeLang(), { hour: "numeric", minute: "2-digit" });
};

/** Condensed rows shown before "Show all" expands the log. Deliberately short: in the rail
 *  the timeline is a glanceable "first beats" summary, and the full log is one click away. */
const TIMELINE_LIMIT = 4;
/** How many publisher chips a grouped join row names before the "+n" overflow chip. */
const GROUP_CHIP_LIMIT = 4;

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border bg-background/50 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-0.5 font-semibold tabular-nums">{value}</div>
    </div>
  );
}

/**
 * Story Intelligence panel — lifecycle / momentum, "new since your last visit", coverage alerts, a
 * collapsed timeline and coverage statistics for one event. Fetched from
 * /api/stories/[id]/intelligence; renders nothing if the engine can't supply it (graceful — the page's
 * coverage list stands on its own). Read-only: it changes no recommendation, report, or read tracking.
 *
 * Lives in the story page's RIGHT RAIL, alongside the other "facts about the coverage" modules
 * (balance, publishers). It is orientation, not a task: high-glance, low-interaction, so it sits
 * beside the article list rather than interrupting it. Everything the hero already states — the
 * freshness badge, the article count, the last-coverage time — is deliberately absent here.
 */
export function StoryIntelligencePanel({ storyId }: { storyId: string }) {
  const { t, timeAgo } = useTranslation();
  const { data, isLoading } = useStoryIntelligence(storyId);
  const [expanded, setExpanded] = React.useState(false);

  // Condense the raw event log (day markers + collapsed join runs) — see lib/story-timeline.
  const rows = React.useMemo(() => condenseTimeline(data?.timeline ?? []), [data?.timeline]);

  if (isLoading) {
    return <Skeleton className="h-56 rounded-lg" />;
  }
  if (!data) return null;

  const visibleRows = expanded ? rows : rows.slice(0, TIMELINE_LIMIT);
  const hiddenCount = rows.length - visibleRows.length;

  const { lifecycle, momentum, newSinceLastVisit: nsv, alerts } = data;
  const cs = data.coverageStatistics;
  const mo = MOMENTUM_META[momentum.state] ?? MOMENTUM_META.Stable;
  const MoIcon = mo.icon;

  return (
    <section className="rounded-lg border bg-card p-4 shadow-soft">
      {/* `font-sans`: a tracked-uppercase kicker, not a headline — it opts out of the h1–h3
          display-face default so it matches every other kicker on the page. */}
      <h2 className="inline-flex items-center gap-1.5 font-sans text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        <Activity className="h-4 w-4" /> {t("storyIntel.title")}
      </h2>

      {/* Status: lifecycle + momentum. The freshness badge lives in the hero — repeating it here
          said "Breaking" twice whenever the band and the lifecycle stage coincided, which for a
          fast-moving story is most of the time. */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
            LIFECYCLE_STYLE[lifecycle],
          )}
        >
          {t(`storyIntel.lifecycle.${lifecycle}`)}
        </span>
        <span className={cn("inline-flex items-center gap-1 text-xs font-medium", mo.className)}>
          <MoIcon className="h-3.5 w-3.5" /> {t(`storyIntel.momentum.${momentum.state}`)}
          {momentum.newPublishers > 0 && (
            <span className="text-muted-foreground">· {t("storyIntel.plusNew", { n: momentum.newPublishers })}</span>
          )}
        </span>
      </div>

      {/* New since your last visit */}
      {nsv.count > 0 && (
        <div className="mt-4 rounded-md border border-primary/20 bg-primary/5 p-3">
          <div className="flex items-center gap-1.5 text-sm font-medium text-primary">
            <Sparkles className="h-4 w-4" />
            {nsv.count === 1
              ? t("storyIntel.newArticleOne", { n: nsv.count })
              : t("storyIntel.newArticles", { n: nsv.count })}
            {nsv.lastVisited && (
              <span className="font-normal text-muted-foreground">· {t("storyIntel.lastRead", { time: timeAgo(nsv.lastVisited) })}</span>
            )}
          </div>
          {(nsv.publishers.length > 0 || nsv.perspectives.length > 0) && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
              {nsv.publishers.map((p) => (
                <span key={p} className="rounded-full bg-background px-2 py-0.5 ring-1 ring-border">
                  {p}
                </span>
              ))}
              {nsv.perspectives.map((b) => (
                <span
                  key={b}
                  className="rounded-full px-2 py-0.5 font-medium ring-1"
                  style={{ color: LEAN_META[b]?.color, borderColor: LEAN_META[b]?.color }}
                >
                  {t("storyIntel.newPerspective", { label: t(`filter.${b}`) })}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Coverage alerts (informational) */}
      {alerts.length > 0 && (
        <ul className="mt-4 space-y-1.5">
          {alerts.map((a, i) => (
            <li key={`${a.type}-${i}`} className="flex items-start gap-2 text-sm text-muted-foreground">
              <Bell className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
              <span>{a.message}</span>
            </li>
          ))}
        </ul>
      )}

      {/* Coverage statistics */}
      <div className="mt-4 grid grid-cols-2 gap-2">
        <Stat label={t("storyIntel.perDay")} value={cs.coverageVelocityPerDay.toFixed(1)} />
        <Stat
          label={t("storyIntel.recentVsPrior")}
          value={`${cs.coverageGrowth.recent}/${cs.coverageGrowth.prior}`}
        />
        <Stat
          label={t("storyIntel.span")}
          value={cs.coverageDurationHours >= 24 ? `${(cs.coverageDurationHours / 24).toFixed(1)}d` : `${Math.round(cs.coverageDurationHours)}h`}
        />
      </div>

      {/* Condensed timeline: day dividers carry the date ONCE, rows carry only a time of day, and
          consecutive publisher joins collapse into one row of chips — a 20-event pile-on reads as
          a handful of beats instead of twenty near-identical lines. */}
      {rows.length > 0 && (
        <div className="mt-5">
          <h3 className="mb-2 flex items-center gap-1.5 font-sans text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <Gauge className="h-3.5 w-3.5" /> {t("storyIntel.coverageTimeline")}
          </h3>
          <ol className="relative space-y-3 border-l border-border pl-4">
            {visibleRows.map((row, i) => {
              if (row.kind === "day") {
                return (
                  <li key={`day-${row.iso}`} className="relative list-none pt-1 first:pt-0">
                    <span className="text-[0.68rem] font-semibold uppercase tracking-wider text-muted-foreground">
                      {fmtDate(row.iso)}
                    </span>
                  </li>
                );
              }

              if (row.kind === "joins") {
                const shown = row.publishers.slice(0, GROUP_CHIP_LIMIT);
                const overflow = row.publishers.length - shown.length;
                return (
                  <li key={`joins-${row.date}-${i}`} className="relative">
                    <span className="absolute -left-[1.35rem] flex h-4 w-4 items-center justify-center rounded-full bg-card ring-1 ring-border">
                      <UserPlus className="h-3 w-3" />
                    </span>
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-sm">
                        {t("storyIntel.joinedGroup", { n: row.publishers.length })}
                      </span>
                      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                        {fmtTime(row.date)}
                      </span>
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
                      {shown.map((p) => (
                        <span key={p} className="rounded-full bg-background px-2 py-0.5 ring-1 ring-border">
                          {p}
                        </span>
                      ))}
                      {overflow > 0 && (
                        <span className="rounded-full bg-background px-2 py-0.5 tabular-nums text-muted-foreground ring-1 ring-border">
                          +{overflow}
                        </span>
                      )}
                    </div>
                  </li>
                );
              }

              const e = row.event;
              const Icon = TIMELINE_ICON[e.type] ?? ArrowRight;
              const accent = e.type === "perspective_expansion" && e.perspective
                ? LEAN_META[e.perspective]?.color
                : undefined;
              return (
                <li key={`${e.type}-${e.date}-${i}`} className="relative">
                  <span
                    className="absolute -left-[1.35rem] flex h-4 w-4 items-center justify-center rounded-full bg-card ring-1 ring-border"
                    style={accent ? { color: accent } : undefined}
                  >
                    <Icon className="h-3 w-3" style={accent ? { color: accent } : undefined} />
                  </span>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-sm">{e.label}</span>
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{fmtTime(e.date)}</span>
                  </div>
                </li>
              );
            })}
          </ol>

          {hiddenCount > 0 && (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="mt-3 inline-flex items-center gap-1 rounded text-xs font-medium text-primary transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              <ChevronDown className="h-3.5 w-3.5" aria-hidden />
              {t("storyIntel.showAllEvents", { n: rows.length })}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
