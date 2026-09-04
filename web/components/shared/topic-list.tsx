"use client";

import * as React from "react";
import Link from "next/link";
import { Check, Plus } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The "Similar news topics" index — one design, two surfaces.
 *
 * It began as a local component inside the desktop home page, driving that page's in-page topic
 * filter. The story page now needs the same list for a different job (the topics THIS story is
 * about, each one a link to the stories carrying it), and the requirement was explicitly that the
 * existing design not change. Two copies of the same markup would have satisfied that on the day
 * and drifted by the second edit, so the markup moved here and both callers render it — the design
 * is now shared by construction rather than by discipline.
 *
 * What varies between the two is only how a row ACTS, and that is the prop: `onSelect` for a
 * filter the page applies to itself, `href` for a link. A row renders as a button or an anchor
 * accordingly, because "toggles a filter" and "goes somewhere" are different promises to a reader
 * and to a screen reader, and a div with a click handler makes both of them badly.
 */
export interface TopicEntry {
  /** Stable key AND the value handed back to `onSelect` — the tag name or the topic. */
  value: string;
  /** What the reader sees. */
  label: string;
  /** Optional count shown on the right (the home index shows story counts; a story's own tags
   *  have nothing to count, so it is simply absent there). */
  count?: number;
  href?: string;
}

export function TopicList({
  items,
  active,
  onSelect,
  labelledBy,
}: {
  items: TopicEntry[];
  /** The currently selected value, if this list drives a filter. */
  active?: string | null;
  /** Filter mode. Ignored for entries that carry an `href`. */
  onSelect?: (value: string | null) => void;
  labelledBy?: string;
}) {
  const { formatCompact } = useTranslation();
  if (items.length === 0) return null;
  return (
    <ul className="mt-2" aria-labelledby={labelledBy}>
      {items.map((entry) => {
        const on = active === entry.value;
        const inner = (
          <>
            <span className="min-w-0 truncate font-medium">{entry.label}</span>
            <span className="inline-flex shrink-0 items-center gap-2 text-[11px] tabular-nums text-muted-foreground">
              {entry.count === undefined ? null : formatCompact(entry.count)}
              {on ? (
                <Check className="h-4 w-4 text-foreground" aria-hidden />
              ) : (
                <Plus className="h-4 w-4" aria-hidden />
              )}
            </span>
          </>
        );
        const className =
          "flex w-full items-center justify-between gap-3 py-2.5 text-left text-[14px] transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
        return (
          <li key={entry.value} className="border-b last:border-b-0">
            {entry.href ? (
              <Link href={entry.href} className={className}>
                {inner}
              </Link>
            ) : (
              <button
                type="button"
                aria-pressed={on}
                onClick={() => onSelect?.(on ? null : entry.value)}
                className={cn(className)}
              >
                {inner}
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Reveal control for a list longer than its initial window.
 *
 * Separate from {@link TopicList} because only one caller needs it, and because "show me the rest"
 * is the list's owner's decision — the story page opens with the tags worth reading and keeps the
 * long tail one press away, while the home index has a curated length already.
 */
export function ShowAllButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <div className="mt-3 flex justify-center">
      <button
        type="button"
        onClick={onClick}
        className="rounded-md border px-4 py-1.5 text-[13px] font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {label}
      </button>
    </div>
  );
}
