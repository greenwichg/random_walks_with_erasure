"use client";

import { ChevronDown } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

export interface FilterOption {
  value: string;
  /** Text, or a node (e.g. CountryBadge); its accessible text is the option label. */
  label: React.ReactNode;
  /**
   * How many results this option would return, rendered as a trailing count badge.
   *
   * For a filter whose options are a fixed vocabulary rather than a list derived from the data,
   * where the pickers that CAN drop empty options (country, coverage gaps) instead answer the
   * question by omission. A fixed list cannot do that without its contents flickering between
   * page states, so it says the number out loud — including `0`, which is the answer a reader most
   * needs before spending a click.
   */
  count?: number;
}

/**
 * A compact single-select dropdown filter. When `resettable` (the default), an `all` reset row
 * labeled `allLabel` precedes the options — the "show everything" state for real filters
 * (topic / publisher / covered-by / emotion). Set `resettable={false}` for a mandatory single-choice
 * such as Sort, which has no "all" state: rendering the reset row there both duplicates whichever
 * option shares its label and lets the caller emit an out-of-contract `all` value.
 */
export function FilterSelect({
  label,
  description,
  value,
  options,
  onChange,
  allLabel = "All",
  resettable = true,
}: {
  label: string;
  /** One line under the menu heading, for a filter whose name cannot carry its whole rule.
   *  "Covered by" is the case it exists for: the label says which side, but not that a story
   *  matches on ANY coverage from that side rather than on the story's own lean. */
  description?: string;
  value: string;
  options: FilterOption[];
  onChange: (v: string) => void;
  allLabel?: string;
  resettable?: boolean;
}) {
  const active = value !== "all";
  const current = options.find((o) => o.value === value)?.label;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          className={cn(
            "inline-flex h-9 items-center gap-1.5 rounded-lg border bg-card px-3 text-sm font-medium transition-colors hover:bg-accent",
            active && "border-primary/30 bg-primary/5 text-primary",
          )}
        >
          {label}
          {active && current && <span className="text-xs opacity-80">· {current}</span>}
          <ChevronDown className="h-3.5 w-3.5 opacity-60" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-72 overflow-y-auto">
        <DropdownMenuLabel>{label}</DropdownMenuLabel>
        {description && (
          // `max-w` + wrapping, because this is a sentence rather than a menu row: the dropdown
          // sizes itself to its widest child, and an unwrapped one would stretch the whole menu.
          <p className="max-w-[16rem] whitespace-normal px-2 pb-1.5 text-xs font-normal leading-snug text-muted-foreground">
            {description}
          </p>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup value={value} onValueChange={onChange}>
          {resettable && <DropdownMenuRadioItem value="all">{allLabel}</DropdownMenuRadioItem>}
          {options.map((o) => (
            <DropdownMenuRadioItem key={o.value} value={o.value}>
              {o.label}
              {o.count !== undefined && (
                // `ml-auto` pushes it to the row's trailing edge so the numbers form a column the
                // eye can compare; `tabular-nums` keeps that column from shifting between 9 and 10.
                // Muted, and dimmer still at zero — an empty lens should read as unavailable
                // without becoming a second kind of control.
                <span
                  className={cn(
                    "ml-auto pl-3 text-xs tabular-nums",
                    o.count === 0 ? "text-muted-foreground/50" : "text-muted-foreground",
                  )}
                >
                  {o.count}
                </span>
              )}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
