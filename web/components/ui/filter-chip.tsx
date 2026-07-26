"use client";

import { cn } from "@/lib/utils";

/**
 * The one filter/toggle chip (design system): a pill button with an active state and an optional
 * count. Extracted from the identical private `Chip`s the trending rail and the coverage list had
 * each grown — one implementation, one hover/focus/active treatment everywhere.
 *
 * `aria-pressed` communicates toggle state; pass `title` only when the label alone is ambiguous.
 */
export function FilterChip({
  label,
  count,
  active,
  onClick,
  className,
}: {
  label: string;
  count?: number;
  active: boolean;
  onClick: () => void;
  className?: string;
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
        className,
      )}
    >
      {label}
      {count != null && <span className="tabular-nums opacity-60">{count}</span>}
    </button>
  );
}
