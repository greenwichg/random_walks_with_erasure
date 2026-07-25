"use client";

import type { TopicCount } from "@/lib/home";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The trending rail — the real catalog topics carrying today's coverage, ranked by depth.
 *
 * It filters the page IN PLACE rather than navigating: the home page already holds the story
 * payload these chips describe, so selecting a topic is instant and costs no request. (It also
 * keeps the rail honest — `/stories` has no topic query-param contract, so a link there would
 * silently ignore the selection.)
 *
 * Implemented as a single-select toolbar: arrow-key semantics come free from native buttons, and
 * `aria-pressed` communicates the active filter to assistive tech.
 */
export function TrendingTopicsRail({
  topics,
  active,
  onSelect,
}: {
  topics: TopicCount[];
  /** The selected topic, or null for "all". */
  active: string | null;
  onSelect: (topic: string | null) => void;
}) {
  const { t } = useTranslation();
  if (topics.length === 0) return null;

  return (
    <div
      role="toolbar"
      aria-label={t("home.trending.title")}
      // Edge-to-edge scroll on small screens; the negative margin + padding keeps the first and
      // last chip flush with the page gutter instead of clipped against it.
      className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
    >
      <Chip label={t("home.trending.all")} active={active === null} onClick={() => onSelect(null)} />
      {topics.map((entry) => (
        <Chip
          key={entry.topic}
          label={entry.topic}
          count={entry.count}
          active={active === entry.topic}
          onClick={() => onSelect(active === entry.topic ? null : entry.topic)}
        />
      ))}
    </div>
  );
}

function Chip({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count?: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        active
          ? "border-primary bg-primary/10 text-primary"
          : "border-border bg-card text-muted-foreground hover:bg-accent hover:text-foreground",
      )}
    >
      {label}
      {count != null && <span className="tabular-nums opacity-60">{count}</span>}
    </button>
  );
}
