"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Search, FileText, Loader2, CornerDownLeft, ArrowRight } from "lucide-react";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { useSearch } from "@/hooks/use-data";
import type { Article } from "@/types/domain";
import { leanBucket, leanLabelKey } from "@/lib/political";
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

  const seeAll = () => {
    onOpenChange(false);
    router.push(`/search?query=${encodeURIComponent(q.trim())}`);
  };

  // A result opens the canonical publisher URL (the Read flow the extension captures); with no usable
  // link, fall back to the full search page.
  const openArticle = (a: Article) => {
    onOpenChange(false);
    const href = a.url && /^https?:\/\//i.test(a.url) ? a.url : null;
    if (href) window.open(href, "_blank", "noopener,noreferrer");
    else router.push(`/search?query=${encodeURIComponent(q.trim())}`);
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" hideClose className="w-full border-none bg-transparent p-0 shadow-none sm:w-[34rem]">
        <div className="mx-auto mt-[10vh] w-[calc(100%-2rem)] max-w-xl overflow-hidden rounded-2xl border bg-popover shadow-card sm:w-full">
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
                    <Badge variant={leanBucket(a.lean)} className="ml-auto shrink-0">
                      {t(leanLabelKey(a.lean))}
                    </Badge>
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
