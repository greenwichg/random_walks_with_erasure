"use client";

import * as React from "react";
import { Search, ListFilter, CalendarDays, X } from "lucide-react";
import type { EmotionShare } from "@/types/domain";
import { useHistory } from "@/hooks/use-data";
import { leanBucket, dominantEmotion } from "@/lib/political";
import { EMOTION_META, LEAN_META } from "@/lib/metrics";
import { PageContainer } from "@/components/layout/page-container";
import { ArticleRow } from "@/components/shared/article-row";
import { FilterSelect } from "@/components/shared/filter-select";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { timeAgo, cn } from "@/lib/utils";

type View = "timeline" | "calendar";

export default function HistoryPage() {
  const { data, isLoading, isError, refetch } = useHistory();
  const [q, setQ] = React.useState("");
  const [topic, setTopic] = React.useState("all");
  const [publisher, setPublisher] = React.useState("all");
  const [lean, setLean] = React.useState("all");
  const [emotion, setEmotion] = React.useState("all");
  const [view, setView] = React.useState<View>("timeline");

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

  const anyFilter = q || topic !== "all" || publisher !== "all" || lean !== "all" || emotion !== "all";
  const reset = () => {
    setQ("");
    setTopic("all");
    setPublisher("all");
    setLean("all");
    setEmotion("all");
  };

  // group by calendar day
  const groups = React.useMemo(() => {
    const map = new Map<string, typeof filtered>();
    filtered.forEach((h) => {
      const key = new Date(h.readAt).toLocaleDateString("en", { weekday: "long", month: "long", day: "numeric" });
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(h);
    });
    return [...map.entries()];
  }, [filtered]);

  return (
    <PageContainer>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Reading History</h1>
          <p className="mt-1 text-sm text-muted-foreground">Everything you've read, searchable and filterable.</p>
        </div>
        <div className="inline-flex rounded-lg border bg-muted p-1">
          <ViewToggle icon={ListFilter} label="Timeline" active={view === "timeline"} onClick={() => setView("timeline")} />
          <ViewToggle icon={CalendarDays} label="Calendar" active={view === "calendar"} onClick={() => setView("calendar")} />
        </div>
      </div>

      {/* filter bar */}
      <div className="mb-6 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[12rem] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search your reading…" className="pl-9" />
        </div>
        <FilterSelect label="Topic" value={topic} options={topics.map((t) => ({ value: t, label: t }))} onChange={setTopic} />
        <FilterSelect label="Publisher" value={publisher} options={publishers.map((p) => ({ value: p, label: p }))} onChange={setPublisher} />
        <FilterSelect
          label="Lean"
          value={lean}
          options={(["left", "center", "right"] as const).map((l) => ({ value: l, label: LEAN_META[l].label }))}
          onChange={setLean}
        />
        <FilterSelect
          label="Emotion"
          value={emotion}
          options={(Object.keys(EMOTION_META) as (keyof EmotionShare)[]).map((e) => ({ value: e, label: EMOTION_META[e].label }))}
          onChange={setEmotion}
        />
        {anyFilter && (
          <Button variant="ghost" size="sm" onClick={reset} className="text-muted-foreground">
            <X className="h-4 w-4" /> Clear
          </Button>
        )}
      </div>

      {isLoading && (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && filtered.length === 0 && (
        <EmptyState icon={Search} title="No matches" description="Try clearing a filter or searching for something else." />
      )}

      {data && view === "timeline" && filtered.length > 0 && (
        <div className="space-y-8">
          {groups.map(([day, items]) => (
            <div key={day}>
              <div className="mb-3 flex items-center gap-3">
                <h3 className="text-sm font-semibold">{day}</h3>
                <span className="text-xs text-muted-foreground">{items.length} articles</span>
                <div className="h-px flex-1 bg-border" />
              </div>
              <div className="space-y-3">
                {items.map((h, i) => (
                  <ArticleRow key={h.id} article={h.article} completed={h.completed} meta={timeAgo(h.readAt)} index={i} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {data && view === "calendar" && <CalendarView entries={filtered} />}
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

/** A simple 5-week reading-activity heatmap. */
function CalendarView({ entries }: { entries: { readAt: string }[] }) {
  const counts = new Map<string, number>();
  entries.forEach((e) => {
    const key = new Date(e.readAt).toDateString();
    counts.set(key, (counts.get(key) ?? 0) + 1);
  });
  const days = Array.from({ length: 35 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (34 - i));
    return { date: d, count: counts.get(d.toDateString()) ?? 0 };
  });
  const max = Math.max(...days.map((d) => d.count), 1);
  const level = (c: number) => (c === 0 ? 0 : Math.ceil((c / max) * 4));
  const shades = ["bg-muted", "bg-primary/25", "bg-primary/45", "bg-primary/70", "bg-primary"];

  return (
    <div className="rounded-lg border bg-card p-6">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold">Last 5 weeks</h3>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          Less
          {shades.map((s, i) => (
            <span key={i} className={cn("h-3 w-3 rounded-sm", s)} />
          ))}
          More
        </div>
      </div>
      <div className="grid grid-flow-col grid-rows-7 gap-1.5">
        {days.map((d, i) => (
          <div
            key={i}
            title={`${d.date.toLocaleDateString("en", { month: "short", day: "numeric" })} · ${d.count} read`}
            className={cn("aspect-square rounded-sm", shades[level(d.count)])}
          />
        ))}
      </div>
    </div>
  );
}
