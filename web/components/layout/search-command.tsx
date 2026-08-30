"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Search, FileText, Loader2, CornerDownLeft, ArrowRight } from "lucide-react";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { LeanBadge } from "@/components/shared/article-badges";
import { useSearch } from "@/hooks/use-data";
import type { Article } from "@ih/core/domain/types";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The height the reader can actually SEE, tracked while the overlay is open.
 *
 * `vh` is the largest the viewport ever gets — the URL bar retracted — so on a phone it over-reports
 * from the moment the page loads. `dvh` fixes that much, but neither knows about the software
 * keyboard: on iOS Safari the layout viewport does not shrink when the keys come up, so a panel
 * sized in either unit keeps its full height and its lower half — the results, and the scrolling
 * needed to reach them — sits underneath the keyboard.
 *
 * `visualViewport` is the only API that reports what is genuinely on screen, and it fires `resize`
 * as the keyboard opens and closes. Null until measured, and null where the API is missing (older
 * browsers, SSR), so the caller can fall back to `dvh` rather than render nothing.
 */
function useVisibleHeight(open: boolean): number | null {
  const [height, setHeight] = React.useState<number | null>(null);

  React.useEffect(() => {
    const vv = typeof window === "undefined" ? null : window.visualViewport;
    if (!open || !vv) return;
    const sync = () => setHeight(vv.height);
    sync();
    // `scroll` as well as `resize`: iOS reports the keyboard partly as a scroll of the visual
    // viewport, and a resize alone would miss the settled height.
    vv.addEventListener("resize", sync);
    vv.addEventListener("scroll", sync);
    return () => {
      vv.removeEventListener("resize", sync);
      vv.removeEventListener("scroll", sync);
      setHeight(null);            // never leave a stale height to size the NEXT opening
    };
  }, [open]);

  return height;
}

/** ⌘K / global search — a quick launcher over the live FeedArticle catalog, backed by /api/search. */
export function SearchCommand({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const { t } = useTranslation();
  const [q, setQ] = React.useState("");
  const router = useRouter();
  const active = q.trim().length > 1;
  const { data, isFetching } = useSearch({ query: q.trim(), limit: 7 }, active);
  const results = data?.results ?? [];
  const visibleHeight = useVisibleHeight(open);

  React.useEffect(() => {
    if (!open) setQ("");
  }, [open]);

  // Closing in order to NAVIGATE must not also pop our history entry (see the effect below) —
  // that would undo the navigation we just started.
  const navigatingRef = React.useRef(false);

  /**
   * The Back gesture closes the overlay instead of leaving the page.
   *
   * On a phone, Back IS the dismiss gesture — a full-screen overlay that ignores it and navigates
   * the page away instead is the worst version of "I can't get out of this". Radix handles Escape
   * and outside-press but knows nothing about history, so one entry is pushed while the overlay is
   * open and popping it closes. `pushState` with no URL leaves the address bar untouched.
   */
  React.useEffect(() => {
    if (!open) return;
    window.history.pushState({ rweSearch: true }, "");
    const onPop = () => onOpenChange(false);
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      // Closing by any OTHER route (Cancel, Escape, outside press) has to take the entry back out,
      // or the reader's next Back silently does nothing. Guarded twice: not when we closed in
      // order to navigate, and not when a real Back already popped it — after that pop the state
      // on top is the previous page's, so the flag is gone.
      if (navigatingRef.current) {
        navigatingRef.current = false;
        return;
      }
      if (window.history.state?.rweSearch) window.history.back();
    };
  }, [open, onOpenChange]);

  const seeAll = () => {
    navigatingRef.current = true;
    onOpenChange(false);
    router.push(`/search?query=${encodeURIComponent(q.trim())}`);
  };

  // A result opens the canonical publisher URL (the Read flow the extension captures); with no usable
  // link, fall back to the full search page.
  const openArticle = (a: Article) => {
    const href = a.url && /^https?:\/\//i.test(a.url) ? a.url : null;
    // Only the fallback moves THIS tab; opening the publisher in a new one leaves our history
    // alone, so that path still wants the pushed entry taken back out.
    if (!href) navigatingRef.current = true;
    onOpenChange(false);
    if (href) window.open(href, "_blank", "noopener,noreferrer");
    else router.push(`/search?query=${encodeURIComponent(q.trim())}`);
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" hideClose className="w-full border-none bg-transparent p-0 shadow-none sm:w-[34rem]">
        {/* Radix names the dialog from its Title. There was none, so the overlay announced itself
            as an unlabelled dialog (and Radix warns about it in the console). */}
        <SheetTitle className="sr-only">{t("header.search")}</SheetTitle>

        {/* The dismiss-by-tapping-outside target.
            The Sheet's content is `inset-y-0 w-full` on a phone, so it covers the whole screen and
            the Radix Overlay — the thing that normally takes an outside press — is entirely behind
            it. There was no "outside" left to tap. This wrapper fills that area and closes on a
            press that lands on IT rather than bubbling up from the card, so a tap on the input or a
            result is untouched. Pointer-only on purpose: it is a convenience beside Cancel and
            Escape, which are the accessible paths, so it must not become a tab stop of its own. */}
        {/* NOT `aria-hidden`: that was the first attempt and it hid the whole panel — input,
            results and Cancel — from assistive tech, since aria-hidden applies to descendants too.
            A plain div with no role and no tabindex already contributes nothing to the tree. */}
        <div
          className="min-h-full w-full"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) onOpenChange(false);
          }}
        >
        {/* A COLUMN, bounded by what is on screen, so the results take every pixel the header does
            not. `--search-max-h` prefers the measured visual viewport (the only figure that accounts
            for the keyboard) and falls back to `dvh`, which at least tracks the URL bar. The 1.5rem
            both arms subtract is the panel's own `mt-3` plus an equal gap below it, so it never
            reaches the very edge. Both are dropped at `sm`, where the panel stays content-height
            under its 10vh offset exactly as before. */}
        <div
          style={{
            "--search-max-h": visibleHeight ? `${visibleHeight - 24}px` : "calc(100dvh - 1.5rem)",
          } as React.CSSProperties}
          className="mx-auto mt-3 flex max-h-[var(--search-max-h)] w-[calc(100%-1.5rem)] max-w-xl flex-col overflow-hidden rounded-2xl border bg-popover shadow-card sm:mt-[10vh] sm:max-h-none sm:w-full"
        >
          <form
            className="flex shrink-0 items-center gap-2 border-b px-3 sm:gap-3 sm:px-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (active) seeAll();
            }}
          >
            {isFetching ? (
              <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
            ) : (
              <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
            )}
            {/* `text-base` on mobile is not a type choice, it is the iOS zoom threshold: Safari
                magnifies the whole page when a focused field is under 16px, and does not undo it
                when the field blurs. `Input`'s base is `text-sm` (14px), so opening search zoomed
                the page and left it zoomed — which is what an "oversized" field and a cramped panel
                look like afterwards. Desktop keeps 14px and the 3rem row it always had. */}
            <Input
              autoFocus
              inputMode="search"
              enterKeyHint="search"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("searchCmd.placeholder")}
              className="h-11 border-0 bg-transparent px-0 text-base shadow-none focus-visible:ring-0 sm:h-12 sm:text-sm"
            />
            {/* Dismiss. It sits in the INPUT'S OWN ROW, which is what keeps it reachable when the
                keyboard is up: the browser scrolls the focused field into view, and this rides
                along with it. A bar pinned to the bottom of the overlay would be exactly what the
                keyboard covers, and `position: fixed` on iOS Safari is measured against the layout
                viewport, so it would sit *behind* the keyboard rather than above it.
                Desktop keeps the ESC hint and gains nothing it did not have. */}
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="shrink-0 rounded-md px-2 py-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:hidden"
            >
              {t("searchCmd.close")}
            </button>
            <kbd className="hidden rounded border bg-muted px-1.5 py-0.5 text-[0.65rem] text-muted-foreground sm:block">
              ESC
            </kbd>
          </form>

          {/* `flex-1 min-h-0` is what hands the results everything the header leaves: the column is
              bounded above, so the list takes the remainder and scrolls inside it — and shrinks with
              the visible height as the keyboard opens, instead of extending underneath it.
              `min-h-0` is load-bearing; a flex child's default `min-height: auto` refuses to shrink
              below its content and the panel would grow past the screen instead of scrolling.
              Desktop opts back out to the fixed 50vh box it has always had.
              `overscroll-contain` keeps a flick at the end of the list from scrolling the page
              behind the overlay. */}
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-2 sm:max-h-[50vh] sm:flex-none">
            {!active && (
              <p className="px-3 py-8 text-center text-sm text-muted-foreground">{t("searchCmd.hint")}</p>
            )}
            {active && results.length === 0 && !isFetching && (
              <p className="px-3 py-8 text-center text-sm text-muted-foreground">{t("searchCmd.noMatches", { q: q.trim() })}</p>
            )}

            {results.length > 0 && (
              <div className="mb-1">
                <p className="px-3 py-1.5 text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground/70">
                  {t("analytics.articles")}
                </p>
                {results.map((a) => (
                  <Row key={a.id} icon={FileText} onClick={() => openArticle(a)}>
                    <span className="truncate">{a.headline}</span>
                    <LeanBadge lean={a.lean} className="ml-auto shrink-0" />
                  </Row>
                ))}
                <Row icon={ArrowRight} onClick={seeAll}>
                  <span className="text-muted-foreground">
                    {t("searchCmd.seeAll", { n: data?.total ?? results.length, q: q.trim() })}
                  </span>
                </Row>
              </div>
            )}
          </div>
        </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Row({
  icon: Icon,
  onClick,
  children,
}: {
  icon: React.ElementType;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "group flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm transition-colors hover:bg-accent",
      )}
    >
      <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
      {children}
      <CornerDownLeft className="h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
    </button>
  );
}
