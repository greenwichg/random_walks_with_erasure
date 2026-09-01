"use client";

import * as React from "react";
import { Check, ChevronDown } from "lucide-react";
import { CountryBadge } from "@/components/shared/country-badge";
import { countryName } from "@ih/core/logic/countries";
import { activeLang } from "@/lib/active-lang";
import { matchesCountry } from "@/lib/country-search";
import { usePopoverPlacement } from "@/components/shared/use-popover-placement";
import { cn } from "@/lib/utils";

/**
 * The one searchable country popover (Location Intelligence UX): a compact trigger chip that
 * opens a search-first panel over the full country list, replacing the 210-chip walls the
 * settings pickers had each grown.
 *
 * Selection stays the CALLER's state — this component never reorders, filters or caps the
 * `options` it is given beyond the reader's live search, so the three pickers keep their own
 * orderings (For You ranks by located coverage; Places keeps the API's alphabetical order) and
 * their own caps. `multi` keeps the panel open across toggles (adding five followed places is
 * one open, five taps); single-select closes on pick, which is the whole gesture.
 *
 * Not Radix DropdownMenu on purpose: a menu's typeahead fights a real search input for every
 * printable key. The panel is a small labelled dialog — outside-press and Escape close it, focus
 * lands in the search box on open, and the trigger carries `aria-expanded`.
 */
export function CountryPicker({
  options,
  isSelected,
  onToggle,
  triggerLabel,
  searchPlaceholder,
  noMatchLabel,
  multi = false,
  full = false,
  fullNote,
  dialogLabel,
  className,
}: {
  /** Full option list, in the CALLER's order; `articles` is unused here but kept so callers can
   *  pass their query rows through unchanged. */
  options: ReadonlyArray<{ country: string }>;
  isSelected: (code: string) => boolean;
  onToggle: (code: string) => void;
  triggerLabel: React.ReactNode;
  searchPlaceholder: string;
  /** Rendered when a query matches nothing; receives the query so the copy can quote it. */
  noMatchLabel: (q: string) => React.ReactNode;
  multi?: boolean;
  /** Multi at its cap: unselected rows disable and `fullNote` explains why. */
  full?: boolean;
  fullNote?: string;
  dialogLabel: string;
  className?: string;
}) {
  const lang = activeLang();
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const rootRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const close = React.useCallback(() => setOpen(false), []);
  // Placement + dismissal live in the shared hook (see use-popover-placement.ts for the
  // viewport-clamping and flip rationale, learned on this component's mobile debut).
  const pos = usePopoverPlacement(rootRef, open, close);

  React.useEffect(() => {
    if (open && pos) inputRef.current?.focus();
  }, [open, pos]);

  const shown = React.useMemo(
    () => options.filter((c) => matchesCountry(c.country, countryName(c.country, lang), query)),
    [options, query, lang],
  );

  return (
    <div ref={rootRef} className={cn("relative inline-block", className)}>
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => {
          setOpen((v) => !v);
          setQuery("");
        }}
        className={cn(
          "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full border border-dashed px-3 py-1.5 text-xs font-medium transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          open ? "border-primary/40 bg-primary/10 text-primary" : "text-muted-foreground hover:bg-accent hover:text-foreground",
        )}
      >
        {triggerLabel}
        <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
      </button>
      {open && pos && (
        <div
          role="dialog"
          aria-label={dialogLabel}
          style={{ top: pos.top, bottom: pos.bottom, left: pos.left, width: pos.width }}
          className="fixed z-50 rounded-lg border bg-popover p-2 text-popover-foreground shadow-card"
        >
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={searchPlaceholder}
            aria-label={searchPlaceholder}
            className="mb-2 h-8 w-full rounded-md border bg-background px-3 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          {full && fullNote && <p className="mb-2 px-1 text-xs text-muted-foreground">{fullNote}</p>}
          <div style={{ maxHeight: pos.listMax }} className="overflow-y-auto overscroll-contain">
            {shown.map((c) => {
              const on = isSelected(c.country);
              const blocked = full && !on;
              return (
                <button
                  key={c.country}
                  type="button"
                  role="option"
                  aria-selected={on}
                  disabled={blocked}
                  onClick={() => {
                    onToggle(c.country);
                    if (!multi) setOpen(false);
                  }}
                  className={cn(
                    "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    blocked ? "cursor-not-allowed opacity-40" : "hover:bg-accent",
                    on && "text-primary",
                  )}
                >
                  <CountryBadge code={c.country} />
                  <Check className={cn("h-3.5 w-3.5 shrink-0", on ? "opacity-100" : "opacity-0")} />
                </button>
              );
            })}
            {shown.length === 0 && (
              <p className="px-2 py-3 text-xs text-muted-foreground">{noMatchLabel(query)}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
