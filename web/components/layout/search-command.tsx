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

/** ⌘K / global search — a quick launcher over the live FeedArticle catalog, backed by /api/search. */
export function SearchCommand({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const { t } = useTranslation();
  const [q, setQ] = React.useState("");
  const router = useRouter();
  const active = q.trim().length > 1;
  const { data, isFetching } = useSearch({ query: q.trim(), limit: 7 }, active);
  const results = data?.results ?? [];

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
        <div className="mx-auto mt-4 w-[calc(100%-2rem)] max-w-xl overflow-hidden rounded-2xl border bg-popover shadow-card sm:mt-[10vh] sm:w-full">
          <form
            className="flex items-center gap-3 border-b px-4"
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
            <Input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("searchCmd.placeholder")}
              className="h-12 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
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

          <div className="max-h-[50vh] overflow-y-auto p-2">
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
