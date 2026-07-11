"use client";

import * as React from "react";
import { Search, ListFilter, CalendarDays, X } from "lucide-react";
import type { EmotionShare } from "@/types/domain";
import { useHistory } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import { leanBucket, dominantEmotion } from "@/lib/political";
import { EMOTION_META } from "@/lib/metrics";
import { PageContainer } from "@/components/layout/page-container";
import { ArticleRow } from "@/components/shared/article-row";
import { InsightStrip } from "@/components/history/insight-strip";
import { ReflectionInsights } from "@/components/history/reflection-insights";
import { DailySummary } from "@/components/history/daily-summary";
import { CalendarView } from "@/components/history/calendar-view";
import { FilterSelect } from "@/components/shared/filter-select";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { summarizeHistory, dayKey } from "@/lib/history-insights";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

type View = "timeline" | "calendar";

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

  const topics = React.useMemo(
    () => [...new Set((data ?? []).map((h) => h.article.topic))].sort(),
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
    if (lean !== "all" && leanBucket(a.lean) !== lean) return false;
    if (emotion !== "all" && dominantEmotion(a.emotion) !== emotion) return false;
    return true;
  });

  // A selected Calendar day narrows the Timeline + the summaries to that day (synchronised via the
  // shared local dayKey). The Calendar heatmap itself always shows the full attribute-filtered set.
  const inView = selectedDay ? filtered.filter((h) => dayKey(h.readAt) === selectedDay) : filtered;

  // Descriptive summary of the reads currently in view — powers the Information Health strip, the
  // Reflection/Insights section, and (when a day is selected) the Daily Summary. Reacts to filters.
  const insights = summarizeHistory(inView);

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

  const selectedDayLabel = React.useMemo(() => {
    if (!selectedDay) return "";
    const [y, m, d] = selectedDay.split("-").map(Number);
    return formatDate(new Date(y ?? 0, (m ?? 1) - 1, d ?? 1).toISOString(), {
      weekday: "long",
      month: "long",
      day: "numeric",
    });
  }, [selectedDay, formatDate]);

  // group the in-view reads by calendar day (read time)
  const groups = React.useMemo(() => {
    const map = new Map<string, typeof inView>();
    inView.forEach((h) => {
      const key = formatDate(h.readAt, { weekday: "long", month: "long", day: "numeric" });
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(h);
    });
    return [...map.entries()];
  }, [inView, formatDate]);

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

      {data && filtered.length === 0 && (
        <EmptyState icon={Search} title={t("history.empty.title")} description={t("history.empty.body")} />
      )}

      {data && view === "timeline" && inView.length > 0 && (
        <div className="space-y-8">
          {groups.map(([day, items]) => (
            <div key={day}>
              <div className="mb-3 flex items-center gap-3">
                <h3 className="text-sm font-semibold">{day}</h3>
                <span className="text-xs text-muted-foreground">{t("history.articlesCount", { n: items.length })}</span>
                <div className="h-px flex-1 bg-border" />
              </div>
              <div className="space-y-3">
                {items.map((h, i) => (
                  // The card's relative time is the article's publication time (same field + formatter
                  // Discover uses) — NOT readAt, which drives only the day grouping above.
                  <ArticleRow key={h.id} article={h.article} meta={timeAgo(h.article.publishedAt)} index={i} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {data && view === "calendar" && filtered.length > 0 && (
        <CalendarView entries={filtered} selectedDay={selectedDay} onSelect={setSelectedDay} />
      )}
    </PageContainer>
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
