"use client";

import * as React from "react";
import { Loader2, Search } from "lucide-react";
import { SearchResultList, useSearchLauncher } from "@/components/layout/search-results";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * Desktop search: the header's own field, activated where it already sits.
 *
 * The control looked like a search field and behaved like a button to a modal — pressing it covered
 * the page with a sheet, dimmed what the reader was reading and moved the cursor somewhere else
 * entirely. Searching from a news page is a lookup beside the page, not a departure from it, so the
 * pill now becomes the input it was drawn as: same slot, same row, same height, the page behind it
 * untouched and undimmed, and the answers hanging under the field where the field is.
 *
 * IT IS THE SAME SEARCH. The query, the endpoint, what a result opens and where "see all" goes are
 * in search-results.tsx, shared with the phone's overlay (search-command.tsx) — this file is the
 * box and the dismissal, nothing more.
 *
 * DESKTOP ONLY. Below `lg` the overlay is right: there is no room beside the page for a panel, the
 * software keyboard needs the visible-viewport sizing that shell measures, and Back is the dismiss
 * gesture there. `desktop` decides which of the two the pill opens; until the viewport is known it
 * is the pill, which is what both states start as.
 */
export function HeaderSearch({
  desktop,
  open,
  onOpenChange,
  onOpenOverlay,
}: {
  /** `lg` and up. False (or unknown) sends the press to the overlay instead. */
  desktop: boolean;
  /** The inline field's state. Held by the header so ⌘K can reach it. */
  open: boolean;
  onOpenChange: (v: boolean) => void;
  /** Below `lg`: open the full-screen overlay, unchanged. */
  onOpenOverlay: () => void;
}) {
  const { t } = useTranslation();
  const [q, setQ] = React.useState("");
  const wrapRef = React.useRef<HTMLDivElement>(null);
  const pillRef = React.useRef<HTMLButtonElement>(null);
  const close = React.useCallback(() => onOpenChange(false), [onOpenChange]);
  const { active, isFetching, results, total, seeAll, openArticle } = useSearchLauncher(q, close);

  // A closed field holds no query: reopening starts clean, exactly as the overlay does.
  React.useEffect(() => {
    if (!open) setQ("");
  }, [open]);

  /**
   * Escape, and a press anywhere outside. Both put the header back the way it was.
   *
   * `pointerdown` rather than `click`, so the field is already gone by the time the press the
   * reader actually meant — a link, a menu — resolves underneath. Scoped to the wrapper, so a
   * press on the input, a result or the ESC chip is not "outside".
   */
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onOpenChange(false);
        // Focus goes back where the reader left it, rather than to the top of the document.
        pillRef.current?.focus();
      }
    };
    const onDown = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) onOpenChange(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open, onOpenChange]);

  const expanded = desktop && open;

  return (
    // `relative` only: the panel hangs from this box, and nothing here covers the page.
    <div ref={wrapRef} className="relative">
      {expanded ? (
        <form
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            if (active) seeAll();
          }}
          // The pill's own box, kept: same height, same radius, same right edge. It grows by one
          // step so the activation is visible, and only leftward — the row is `ml-auto`, so the
          // notifications, theme and account controls beside it do not move.
          className={cn(
            "flex h-9 items-center gap-2 rounded-md border border-ring bg-background px-3",
            "ring-1 ring-ring transition-[width] duration-200 lg:w-64 xl:w-80",
          )}
        >
          {isFetching ? (
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" aria-hidden />
          ) : (
            <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          )}
          {/* Autofocused: the press that expands the field is the press that starts the search. */}
          <input
            autoFocus
            type="search"
            inputMode="search"
            enterKeyHint="search"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            aria-label={t("header.search")}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("searchCmd.placeholder")}
            className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground [&::-webkit-search-cancel-button]:hidden"
          />
          {/* The reference's ESC chip, and a real control: a hint that cannot be pressed is a
              worse version of the same pixels. */}
          <button
            type="button"
            aria-label={t("searchCmd.close")}
            onClick={() => {
              onOpenChange(false);
              pillRef.current?.focus();
            }}
            className="shrink-0 rounded border bg-muted px-1.5 py-0.5 text-[0.65rem] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            ESC
          </button>
        </form>
      ) : (
        /* Not a <Button>: it is a pill with a label and a ⌘K hint, so it composes its own box —
           which means it also restates the focus ring and the radius Button provides. */
        <button
          ref={pillRef}
          onClick={() => (desktop ? onOpenChange(true) : onOpenOverlay())}
          aria-label={t("header.search")}
          aria-expanded={desktop ? open : undefined}
          className="hidden h-9 shrink-0 items-center gap-2 rounded-md border bg-background/60 px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background sm:flex lg:w-56 xl:w-72"
        >
          <Search className="h-4 w-4 shrink-0" />
          <span className="lg:hidden">{t("header.search")}</span>
          <span className="hidden min-w-0 flex-1 truncate text-left lg:inline">{t("header.search")}</span>
          <kbd className="rounded border bg-muted px-1.5 py-0.5 text-[0.65rem]">⌘K</kbd>
        </button>
      )}

      {/* The answers, under the field. Right-aligned to it and wider than it, because a headline
          needs the room and there is empty header to the left of the field to take it from. */}
      {expanded && (
        <div className="absolute right-0 top-[calc(100%+0.5rem)] z-30 w-[32rem] overflow-hidden rounded-lg border bg-popover shadow-card">
          <div className="max-h-[60vh] overflow-y-auto overscroll-contain p-2">
            <SearchResultList
              q={q}
              active={active}
              isFetching={isFetching}
              results={results}
              total={total}
              seeAll={seeAll}
              openArticle={openArticle}
            />
          </div>
        </div>
      )}
    </div>
  );
}
