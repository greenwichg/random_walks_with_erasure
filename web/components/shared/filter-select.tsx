"use client";

import * as React from "react";
import { Check, ChevronDown } from "lucide-react";
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
import { useTranslation } from "@/lib/i18n";
import { matchesOption } from "@/lib/country-search";
import { usePopoverPlacement } from "@/components/shared/use-popover-placement";

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

/** Past this many options the dropdown grows a search box — the publisher facet feeds thousands
 *  of rows, and a phone reader met them as one alphabetical wall starting at "01Net.Com". */
const SEARCH_THRESHOLD = 15;

/**
 * A compact single-select dropdown filter. When `resettable` (the default), an `all` reset row
 * labeled `allLabel` precedes the options — the "show everything" state for real filters
 * (topic / publisher / covered-by / emotion). Set `resettable={false}` for a mandatory single-choice
 * such as Sort, which has no "all" state: rendering the reset row there both duplicates whichever
 * option shares its label and lets the caller emit an out-of-contract `all` value.
 *
 * Lists longer than SEARCH_THRESHOLD render as a search-first popover instead of a Radix menu
 * (a menu's typeahead fights a real input for every printable key): same trigger, same rows, the
 * FULL list still scrollable under the box — search narrows, it never removes.
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
  if (options.length > SEARCH_THRESHOLD) {
    return (
      <SearchableFilterSelect
        label={label}
        description={description}
        value={value}
        options={options}
        onChange={onChange}
        allLabel={allLabel}
        resettable={resettable}
        active={active}
        current={current}
      />
    );
  }
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

/** The long-list rendering of FilterSelect: identical trigger, a search-first popover panel on
 *  the shared viewport-clamped placement. Split out so the common short-list path stays byte-
 *  identical Radix. */
function SearchableFilterSelect({
  label,
  description,
  value,
  options,
  onChange,
  allLabel,
  resettable,
  active,
  current,
}: {
  label: string;
  description?: string;
  value: string;
  options: FilterOption[];
  onChange: (v: string) => void;
  allLabel: string;
  resettable: boolean;
  active: boolean;
  current: React.ReactNode;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const rootRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const close = React.useCallback(() => setOpen(false), []);
  const pos = usePopoverPlacement(rootRef, open, close);

  React.useEffect(() => {
    if (open && pos) inputRef.current?.focus();
  }, [open, pos]);

  // Matching folds diacritics like every other searchable list; a non-string label (a badge
  // node) falls back to its wire value, so such rows stay findable rather than vanishing.
  const shown = query
    ? options.filter((o) =>
        matchesOption(typeof o.label === "string" ? o.label : o.value, query))
    : options;

  const row = (v: string, labelNode: React.ReactNode, count?: number) => {
    const on = value === v;
    return (
      <button
        key={v}
        type="button"
        role="option"
        aria-selected={on}
        onClick={() => {
          onChange(v);
          setOpen(false);
        }}
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          on && "text-primary",
        )}
      >
        <Check className={cn("h-3.5 w-3.5 shrink-0", on ? "opacity-100" : "opacity-0")} />
        <span className="min-w-0 flex-1 truncate">{labelNode}</span>
        {count !== undefined && (
          <span
            className={cn(
              "pl-3 text-xs tabular-nums",
              count === 0 ? "text-muted-foreground/50" : "text-muted-foreground",
            )}
          >
            {count}
          </span>
        )}
      </button>
    );
  };

  return (
    <div ref={rootRef} className="relative inline-block">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => {
          setOpen((v) => !v);
          setQuery("");
        }}
        className={cn(
          "inline-flex h-9 items-center gap-1.5 rounded-lg border bg-card px-3 text-sm font-medium transition-colors hover:bg-accent",
          active && "border-primary/30 bg-primary/5 text-primary",
        )}
      >
        {label}
        {active && current && <span className="text-xs opacity-80">· {current}</span>}
        <ChevronDown className="h-3.5 w-3.5 opacity-60" />
      </button>
      {open && pos && (
        <div
          role="dialog"
          aria-label={label}
          style={{ top: pos.top, bottom: pos.bottom, left: pos.left, width: pos.width }}
          className="fixed z-50 rounded-lg border bg-popover p-2 text-popover-foreground shadow-card"
        >
          <div className="px-2 pb-1.5 text-sm font-semibold">{label}</div>
          {description && (
            <p className="px-2 pb-1.5 text-xs font-normal leading-snug text-muted-foreground">
              {description}
            </p>
          )}
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("filter.search")}
            aria-label={t("filter.search")}
            className="mb-2 h-8 w-full rounded-md border bg-background px-3 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <div style={{ maxHeight: pos.listMax }} className="overflow-y-auto overscroll-contain">
            {resettable && !query && row("all", allLabel)}
            {shown.map((o) => row(o.value, o.label, o.count))}
            {shown.length === 0 && (
              <p className="px-2 py-3 text-xs text-muted-foreground">
                {t("filter.noMatch", { q: query })}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
