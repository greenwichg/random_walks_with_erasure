"use client";

import {
  Activity,
  ArrowRight,
  Bell,
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
import { FreshnessBadge } from "@/components/stories/freshness-badge";
import { Skeleton } from "@/components/ui/skeleton";
import type { StoryLifecycle, StoryMomentum, StoryTimelineEventType } from "@/types/domain";
import { LEAN_META } from "@/lib/metrics";
import { cn, timeAgo } from "@/lib/utils";

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

const fmtDate = (iso?: string) => {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border bg-background/50 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-0.5 font-semibold tabular-nums">{value}</div>
    </div>
  );
}

/**
 * Story Intelligence panel — deterministic freshness / lifecycle / momentum, "new since your last
 * visit", coverage alerts, an expanded timeline, and coverage statistics for one event. Fetched from
 * /api/stories/[id]/intelligence; renders nothing if the engine can't supply it (graceful — the page's
 * coverage list stands on its own). Read-only: it changes no recommendation, report, or read tracking.
 */
export function StoryIntelligencePanel({ storyId }: { storyId: string }) {
  const { data, isLoading } = useStoryIntelligence(storyId);

  if (isLoading) {
    return <Skeleton className="mt-6 h-40 rounded-lg" />;
  }
  if (!data) return null;

  const { freshness, lifecycle, momentum, newSinceLastVisit: nsv, timeline, alerts } = data;
  const cs = data.coverageStatistics;
  const mo = MOMENTUM_META[momentum.state] ?? MOMENTUM_META.Stable;
  const MoIcon = mo.icon;

  return (
    <section className="mt-6 rounded-lg border bg-card p-5 shadow-soft">
      <div className="flex items-center justify-between gap-2">
        <h2 className="inline-flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          <Activity className="h-4 w-4" /> Story intelligence
        </h2>
        {data.lastUpdated && (
          <span className="text-xs text-muted-foreground">Latest coverage {timeAgo(data.lastUpdated)}</span>
        )}
      </div>

      {/* State badges */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <FreshnessBadge band={freshness.band} score={freshness.score} showScore />
        <span
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
            LIFECYCLE_STYLE[lifecycle],
          )}
        >
          {lifecycle}
        </span>
        <span className={cn("inline-flex items-center gap-1 text-xs font-medium", mo.className)}>
          <MoIcon className="h-3.5 w-3.5" /> {momentum.state}
          {momentum.newPublishers > 0 && (
            <span className="text-muted-foreground">· +{momentum.newPublishers} new</span>
          )}
        </span>
      </div>

      {/* New since your last visit */}
      {nsv.count > 0 && (
        <div className="mt-4 rounded-md border border-primary/20 bg-primary/5 p-3">
          <div className="flex items-center gap-1.5 text-sm font-medium text-primary">
            <Sparkles className="h-4 w-4" />
            {nsv.count} new {nsv.count === 1 ? "article" : "articles"} since your last visit
            {nsv.lastVisited && (
              <span className="font-normal text-muted-foreground">· you last read this {timeAgo(nsv.lastVisited)}</span>
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
                  New: {LEAN_META[b]?.label ?? b}
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
      <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Articles" value={String(cs.articleCount)} />
        <Stat label="Per day" value={cs.coverageVelocityPerDay.toFixed(1)} />
        <Stat
          label="Recent vs prior"
          value={`${cs.coverageGrowth.recent}/${cs.coverageGrowth.prior}`}
        />
        <Stat
          label="Span"
          value={cs.coverageDurationHours >= 24 ? `${(cs.coverageDurationHours / 24).toFixed(1)}d` : `${Math.round(cs.coverageDurationHours)}h`}
        />
      </div>

      {/* Expanded timeline */}
      {timeline.length > 0 && (
        <div className="mt-5">
          <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <Gauge className="h-3.5 w-3.5" /> Coverage timeline
          </h3>
          <ol className="relative space-y-3 border-l border-border pl-4">
            {timeline.map((e, i) => {
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
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{fmtDate(e.date)}</span>
                  </div>
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </section>
  );
}
