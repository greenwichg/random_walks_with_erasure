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

/* Timeline row geometry, in one place so the node, the time column and the label can never drift
 * out of alignment with each other.
 *
 * `pl-7` (28px) clears the 16px node plus its gutter. The node is 16px wide at `left-0`, so its
 * centre lands on 8px — which is exactly where the spine is drawn. The time column is sized for
 * the widest 12-hour stamp ("11:32 AM") at 11px and comfortably over-serves 24-hour locales; a
 * FIXED width is the point, since a shrink-to-fit column would make the labels start at a
 * different x on every row. */
const ROW = "relative pl-7";
const GRID = "grid grid-cols-[3.5rem_minmax(0,1fr)] items-baseline gap-x-3";
const LABEL = "text-[13px] leading-snug";
const CHIP = "rounded-full bg-muted/70 px-2 py-0.5 text-[11px] leading-normal ring-1 ring-border";

/** A node on the spine: an opaque disc so the thread reads as passing behind it. */
function TimelineNode({ icon: Icon, accent }: { icon: LucideIcon; accent?: string }) {
  return (
    <span
      aria-hidden
      className="absolute left-0 top-0 grid h-4 w-4 place-items-center rounded-full bg-card text-muted-foreground ring-1 ring-border"
      style={accent ? { color: accent } : undefined}
    >
      <Icon className="h-2.5 w-2.5" />
    </span>
  );
}

/** The row's time of day. `dateTime` carries the full instant the label omits, so the machine
 *  reading is complete even though the human one is deliberately just a clock time. */
function Time({ iso }: { iso?: string }) {
  return (
    <time dateTime={iso} className="text-[11px] font-medium tabular-nums text-muted-foreground">
      {fmtTime(iso)}
    </time>
  );
}

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

      {/* THE COVERAGE TIMELINE.
          Three alignment decisions carry the readability, and each replaces something that made
          the old column hard to scan:

          TIME LEADS, in a fixed column. It used to be pushed to the far right by `justify-between`,
          so every row put a ragged gap between the event and its timestamp and the eye had to
          zig-zag. A fixed left column means one straight edge of times and one straight edge of
          labels — the shape a timeline is supposed to have.

          THE SPINE IS A REAL SPINE. One continuous hairline, with every node centred ON it, rather
          than a border-left that the nodes sat beside and half-covered. The day divider masks it
          with its own `bg-card`, so a date reads as a break in the thread instead of a label
          floating next to it.

          CHIPS HANG OFF THE LABEL, not the row: they start where the label starts, so a joins row
          reads as one block of text rather than two unrelated left edges. */}
      {rows.length > 0 && (
        <div className="mt-5">
          <h3 className="mb-3 flex items-center gap-1.5 font-sans text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <Gauge className="h-3.5 w-3.5" /> {t("storyIntel.coverageTimeline")}
          </h3>
          <ol className="relative space-y-3">
            {/* Inset top and bottom so the thread starts and ends at the first and last node
                rather than running past them into the panel's padding. */}
            <span aria-hidden className="absolute bottom-2 left-[8px] top-2 w-px bg-border" />

            {visibleRows.map((row, i) => {
              if (row.kind === "day") {
                return (
                  <li key={`day-${row.iso}`} className="relative list-none pt-2 first:pt-0">
                    <div className="flex items-center gap-2">
                      {/* `bg-card` is what cuts the spine behind the date. */}
                      <span className="bg-card pr-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                        {fmtDate(row.iso)}
                      </span>
                      <span aria-hidden className="h-px flex-1 bg-border" />
                    </div>
                  </li>
                );
              }

              if (row.kind === "joins") {
                const shown = row.publishers.slice(0, GROUP_CHIP_LIMIT);
                const overflow = row.publishers.length - shown.length;
                return (
                  <li key={`joins-${row.date}-${i}`} className={ROW}>
                    <TimelineNode icon={UserPlus} />
                    <div className={GRID}>
                      <Time iso={row.date} />
                      <div className="min-w-0">
                        <p className={LABEL}>{t("storyIntel.joinedGroup", { n: row.publishers.length })}</p>
                        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                          {shown.map((p) => (
                            <span key={p} className={CHIP}>{p}</span>
                          ))}
                          {overflow > 0 && (
                            <span className={cn(CHIP, "tabular-nums text-muted-foreground")}>+{overflow}</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </li>
                );
              }

              const e = row.event;
              const Icon = TIMELINE_ICON[e.type] ?? ArrowRight;
              const accent =
                e.type === "perspective_expansion" && e.perspective
                  ? LEAN_META[e.perspective]?.color
                  : undefined;
              return (
                <li key={`${e.type}-${e.date}-${i}`} className={ROW}>
                  <TimelineNode icon={Icon} accent={accent} />
                  <div className={GRID}>
                    <Time iso={e.date} />
                    {/* The opening beat is the only row given extra weight, and only because the
                        data says it is a different KIND of event — not because it is first. */}
                    <p className={cn(LABEL, e.type === "first_report" && "font-medium")}>{e.label}</p>
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
