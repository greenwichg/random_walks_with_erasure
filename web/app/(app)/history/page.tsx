"use client";

import * as React from "react";
import Link from "next/link";
import { Search, ListFilter, CalendarDays, X, Clock, BookOpen } from "lucide-react";
import type { EmotionShare } from "@/types/domain";
import { useHistory } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { leanBucket, dominantEmotion } from "@/lib/political";
import { EMOTION_META } from "@/lib/metrics";
import { PageContainer } from "@/components/layout/page-container";
import { ArticleRow } from "@/components/shared/article-row";
import { InsightStrip } from "@/components/history/insight-strip";
import { ReflectionInsights } from "@/components/history/reflection-insights";
import { ReadingPatternStrip } from "@/components/history/reading-pattern";
import { DailySummary } from "@/components/history/daily-summary";
import { CalendarView } from "@/components/history/calendar-view";
import { FilterSelect } from "@/components/shared/filter-select";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { summarizeHistory, readingPattern, sessionize, dayKey, type ReadingSession } from "@/lib/history-insights";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

type View = "timeline" | "calendar";
const PAGE_DAYS = 8;

export default function HistoryPage() {
  const { data, isLoading, isError, refetch } = useHistory();
  const { t, formatDate, timeAgo } = useTranslation();
  const [q, setQ] = React.useState("");
  const [topic, setTopic] = React.useState("all");
  const [publisher, setPublisher] = React.useState("all");
  const [lean, setLean] = React.useState("all");
  const [emotion, setEmotion] = React.useState("all");
  const [view, setView] = React.useState<View>("timeline");
  const [selectedDay, setSelectedDay] = React.useState<string | null>(null);
  const [dayLimit, setDayLimit] = React.useState(PAGE_DAYS);

  const topics = React.useMemo(
    () => [...new Set((data ?? []).map((h) => h.article.topic))].filter(Boolean).sort(),
    [data],
  );
  const publishers = React.useMemo(
    () => [...new Set((data ?? []).map((h) => h.article.publisher))].sort(),
    [data],
  );

  const filtered = (data ?? []).filter((h) => {
    const a = h.article;
    if (q && !`${a.headline} ${a.publisher} ${a.topic}`.toLowerCase().includes(q.toLowerCase())) return false;
    if (topic !== "all" && a.topic !== topic) return false;
    if (publisher !== "all" && a.publisher !== publisher) return false;
    if (lean !== "all" && (a.lean == null || leanBucket(a.lean) !== lean)) return false;
    if (emotion !== "all" && dominantEmotion(a.emotion) !== emotion) return false;
    return true;
  });

  // A selected Calendar day narrows the Timeline + the content summaries to that day (synchronised
  // via the shared local dayKey). The Calendar heatmap always shows the full attribute-filtered set.
  const inView = selectedDay ? filtered.filter((h) => dayKey(h.readAt) === selectedDay) : filtered;

  // Content composition of the reads in view (strip, reflection, daily summary).
  const insights = summarizeHistory(inView);
  // Behavioural pattern across all in-filter days (not narrowed to a selected day).
  const pattern = readingPattern(filtered);

  const anyFilter = q || topic !== "all" || publisher !== "all" || lean !== "all" || emotion !== "all";
  const reset = () => {
    setQ("");
    setTopic("all");
    setPublisher("all");
    setLean("all");
    setEmotion("all");
  };

  // Changing an attribute filter can empty the selected day, so clear the selection alongside it.
  React.useEffect(() => {
    setSelectedDay(null);
  }, [q, topic, publisher, lean, emotion]);
  // Any change to what's shown resets pagination to the first page of days.
  React.useEffect(() => {
    setDayLimit(PAGE_DAYS);
  }, [q, topic, publisher, lean, emotion, selectedDay]);

  const selectedDayLabel = React.useMemo(() => {
    if (!selectedDay) return "";
    const [y, m, d] = selectedDay.split("-").map(Number);
    return formatDate(new Date(y ?? 0, (m ?? 1) - 1, d ?? 1).toISOString(), {
      weekday: "long",
      month: "long",
      day: "numeric",
    });
  }, [selectedDay, formatDate]);

  // group the in-view reads by calendar day (read time), newest day first — keyed by the sortable
  // dayKey so the order is the actual date, robust to the store's insert order.
  const groups = React.useMemo(() => {
    const map = new Map<string, { label: string; items: typeof inView }>();
    inView.forEach((h) => {
      const key = dayKey(h.readAt);
      let g = map.get(key);
      if (!g) {
        g = { label: formatDate(h.readAt, { weekday: "long", month: "long", day: "numeric" }), items: [] };
        map.set(key, g);
      }
      g.items.push(h);
    });
    return [...map.entries()].sort((a, b) => (a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : 0));
  }, [inView, formatDate]);
  const shownGroups = groups.slice(0, dayLimit);

  return (
    <PageContainer>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("nav.history")}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t("history.subtitle")}</p>
        </div>
        <div className="inline-flex rounded-lg border bg-muted p-1">
          <ViewToggle icon={ListFilter} label={t("history.timeline")} active={view === "timeline"} onClick={() => setView("timeline")} />
          <ViewToggle icon={CalendarDays} label={t("history.calendar")} active={view === "calendar"} onClick={() => setView("calendar")} />
        </div>
      </div>

      {/* filter bar */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[12rem] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("history.searchPlaceholder")} className="pl-9" />
        </div>
        <FilterSelect label={t("filter.topic")} value={topic} options={topics.map((tp) => ({ value: tp, label: tp }))} onChange={setTopic} />
        <FilterSelect label={t("filter.publisher")} value={publisher} options={publishers.map((p) => ({ value: p, label: p }))} onChange={setPublisher} />
        <FilterSelect
          label={t("filter.lean")}
          value={lean}
          options={(["left", "center", "right"] as const).map((l) => ({ value: l, label: t(`filter.${l}`) }))}
          onChange={setLean}
        />
        <FilterSelect
          label={t("filter.emotion")}
          value={emotion}
          options={(Object.keys(EMOTION_META) as (keyof EmotionShare)[]).map((e) => ({ value: e, label: t(`emotion.${e}`) }))}
          onChange={setEmotion}
        />
        {anyFilter && (
          <Button variant="ghost" size="sm" onClick={reset} className="text-muted-foreground">
            <X className="h-4 w-4" /> {t("common.clear")}
          </Button>
        )}
      </div>

      {/* selected-day chip — a Calendar selection filtering the Timeline; removable */}
      {selectedDay && (
        <div className="mb-6 flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-primary/5 px-3 py-1 text-xs font-medium text-primary">
            <CalendarDays className="h-3.5 w-3.5" />
            {selectedDayLabel}
            <button
              type="button"
              onClick={() => setSelectedDay(null)}
              aria-label={t("history.clearDay")}
              className="-mr-1 ml-0.5 rounded-full p-0.5 transition-colors hover:bg-primary/15"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        </div>
      )}

      {data && filtered.length > 0 && (
        <div className="mb-6 space-y-5">
          {!selectedDay && <ReadingPatternStrip pattern={pattern} />}
          <InsightStrip insights={insights} />
          <ReflectionInsights insights={insights} />
          {selectedDay && inView.length > 0 && <DailySummary insights={insights} dayLabel={selectedDayLabel} />}
        </div>
      )}

      {isLoading && (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && data.length === 0 && (
        <EmptyState
          icon={BookOpen}
          title={t("history.startEmpty.title")}
          description={t("history.startEmpty.body")}
          action={
            <Button asChild>
              <Link href="/discover">{t("history.startEmpty.cta")}</Link>
            </Button>
          }
        />
      )}
      {data && data.length > 0 && filtered.length === 0 && (
        <EmptyState icon={Search} title={t("history.empty.title")} description={t("history.empty.body")} />
      )}

      {data && view === "timeline" && inView.length > 0 && (
        <div className="space-y-8">
          {shownGroups.map(([key, { label, items }]) => (
            <div key={key}>
              <div className="mb-3 flex items-center gap-3">
                <h3 className="text-sm font-semibold">{label}</h3>
                <span className="text-xs text-muted-foreground">{t("history.articlesCount", { n: items.length })}</span>
                <div className="h-px flex-1 bg-border" />
              </div>
              <div className="space-y-5">
                {sessionize(items).map((s) => (
                  <div key={s.end} className="space-y-2.5">
                    <SessionHeader session={s} />
                    <div className="space-y-3">
                      {s.reads.map((h, i) => (
                        // The card's relative time is the article's publication time (same field +
                        // formatter Discover uses) — NOT readAt, which drives the day/session grouping.
                        <ArticleRow key={h.id} article={h.article} meta={timeAgo(h.article.publishedAt)} source={h.openedFrom} index={i} />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
          {groups.length > dayLimit && (
            <div className="flex justify-center pt-2">
              <Button variant="outline" onClick={() => setDayLimit((d) => d + PAGE_DAYS)}>
                {t("history.loadMore")}
              </Button>
            </div>
          )}
        </div>
      )}

      {data && view === "calendar" && filtered.length > 0 && (
        <CalendarView entries={filtered} selectedDay={selectedDay} onSelect={setSelectedDay} />
      )}
    </PageContainer>
  );
}

function SessionHeader({ session }: { session: ReadingSession }) {
  const { t, lang } = useTranslation();
  // Time-only (the day is already in the group header); formatDate uses toLocaleDateString, which
  // would append the date, so format the clock time directly in the active locale.
  const time = (iso: string) => new Date(iso).toLocaleTimeString(lang, { hour: "numeric", minute: "2-digit" });
  // Collapse to a single time when the session's reads fall in the same displayed minute — e.g. a
  // batch recorded milliseconds apart. Compare the FORMATTED times, not the raw ISO (which differs at
  // sub-minute precision); a "3:56 PM – 3:56 PM" range conveys nothing and reads as a bug.
  const startTime = time(session.start);
  const endTime = time(session.end);
  const range = startTime === endTime ? startTime : `${startTime} – ${endTime}`;
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <Clock className="h-3.5 w-3.5 shrink-0" />
      <span className="font-medium">{range}</span>
      <span className="opacity-70">· {t("history.articlesCount", { n: session.reads.length })}</span>
    </div>
  );
}

function ViewToggle({
  icon: Icon,
  label,
  active,
  onClick,
}: {
  icon: React.ElementType;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md px-3 py-1 text-sm font-medium transition-colors",
        active ? "bg-background text-foreground shadow-soft" : "text-muted-foreground hover:text-foreground",
      )}
    >
      <Icon className="h-4 w-4" /> {label}
    </button>
  );
}
