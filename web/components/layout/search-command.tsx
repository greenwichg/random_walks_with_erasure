"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Search, FileText, Newspaper, Loader2, CornerDownLeft } from "lucide-react";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { useSearch } from "@/hooks/use-data";
import { leanBucket, leanLabel } from "@/lib/political";
import { cn } from "@/lib/utils";

/** ⌘K / global search across articles + stories, backed by /api/search. */
export function SearchCommand({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [q, setQ] = React.useState("");
  const router = useRouter();
  const { data, isFetching } = useSearch(q);

  React.useEffect(() => {
    if (!open) setQ("");
  }, [open]);

  const go = (href: string) => {
    onOpenChange(false);
    router.push(href);
  };

  const hasResults = (data?.articles.length ?? 0) + (data?.stories.length ?? 0) > 0;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" hideClose className="w-full border-none bg-transparent p-0 shadow-none sm:w-[34rem]">
        <div className="mx-auto mt-[10vh] w-[calc(100%-2rem)] max-w-xl overflow-hidden rounded-2xl border bg-popover shadow-card sm:w-full">
          <div className="flex items-center gap-3 border-b px-4">
            {isFetching ? (
              <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
            ) : (
              <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
            )}
            <Input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search articles, stories, topics…"
              className="h-12 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
            />
            <kbd className="hidden rounded border bg-muted px-1.5 py-0.5 text-[0.65rem] text-muted-foreground sm:block">
              ESC
            </kbd>
          </div>

          <div className="max-h-[50vh] overflow-y-auto p-2">
            {q.trim().length <= 1 && (
              <p className="px-3 py-8 text-center text-sm text-muted-foreground">
                Type to search your reading and today's stories.
              </p>
            )}
            {q.trim().length > 1 && !hasResults && !isFetching && (
              <p className="px-3 py-8 text-center text-sm text-muted-foreground">No matches for “{q}”.</p>
            )}

            {data?.stories.length ? (
              <Group label="Stories">
                {data.stories.map((s) => (
                  <Row key={s.id} icon={Newspaper} onClick={() => go(`/stories/${s.id}`)}>
                    <span className="truncate">{s.title}</span>
                    <Badge variant="secondary" className="ml-auto shrink-0">
                      {s.topic}
                    </Badge>
                  </Row>
                ))}
              </Group>
            ) : null}

            {data?.articles.length ? (
              <Group label="Articles">
                {data.articles.map((a) => (
                  <Row key={a.id} icon={FileText} onClick={() => go("/history")}>
                    <span className="truncate">{a.headline}</span>
                    <Badge variant={leanBucket(a.lean)} className="ml-auto shrink-0">
                      {leanLabel(a.lean)}
                    </Badge>
                  </Row>
                ))}
              </Group>
            ) : null}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-1">
      <p className="px-3 py-1.5 text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground/70">
        {label}
      </p>
      {children}
    </div>
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
