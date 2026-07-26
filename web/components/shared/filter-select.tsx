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
}

/**
 * A compact single-select dropdown filter. When `resettable` (the default), an `all` reset row
 * labeled `allLabel` precedes the options — the "show everything" state for real filters
 * (topic / publisher / lean / emotion). Set `resettable={false}` for a mandatory single-choice
 * such as Sort, which has no "all" state: rendering the reset row there both duplicates whichever
 * option shares its label and lets the caller emit an out-of-contract `all` value.
 */
export function FilterSelect({
  label,
  value,
  options,
  onChange,
  allLabel = "All",
  resettable = true,
}: {
  label: string;
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
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup value={value} onValueChange={onChange}>
          {resettable && <DropdownMenuRadioItem value="all">{allLabel}</DropdownMenuRadioItem>}
          {options.map((o) => (
            <DropdownMenuRadioItem key={o.value} value={o.value}>
              {o.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
