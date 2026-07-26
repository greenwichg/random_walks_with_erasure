"use client";

import type { TopicCount } from "@/lib/home";
import { FilterChip } from "@/components/ui/filter-chip";
import { useTranslation } from "@/lib/i18n";

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
      <FilterChip label={t("home.trending.all")} active={active === null} onClick={() => onSelect(null)} />
      {topics.map((entry) => (
        <FilterChip
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
