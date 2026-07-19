"use client";

import * as React from "react";
import Link from "next/link";
import { ChevronDown, Link2, Loader2, ScanSearch } from "lucide-react";
import { Logo } from "@/components/layout/logo";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/shared/states";
import { AnalysisResult } from "@/components/analyzer/analysis-result";
import { useAnalyzeUrl } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import type { AnalyzeMetadata } from "@/types/domain";
import { cn } from "@/lib/utils";

/**
 * The anonymous Article Analyzer (A2-Web). Paste a news URL (+ optional page details) → the
 * Information Health analysis of that article. Submit-triggered via `useAnalyzeUrl` (a mutation);
 * every result is rendered honestly through `AnalysisResult`. Standalone page chrome — this route
 * lives outside the authenticated app shell (no sidebar), reachable without an account.
 */
export function AnalyzerFlow() {
  const { t } = useTranslation();
  const analyze = useAnalyzeUrl();
  const [url, setUrl] = React.useState("");
  const [showMeta, setShowMeta] = React.useState(false);
  const [meta, setMeta] = React.useState<AnalyzeMetadata>({});

  const buildMeta = React.useCallback((): AnalyzeMetadata | undefined => {
    const clean: AnalyzeMetadata = {};
    if (meta.title?.trim()) clean.title = meta.title.trim();
    if (meta.description?.trim()) clean.description = meta.description.trim();
    if (meta.category?.trim()) clean.category = meta.category.trim();
    if (meta.outlet?.trim()) clean.outlet = meta.outlet.trim();
    return Object.keys(clean).length ? clean : undefined;
  }, [meta]);

  const onSubmit = React.useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const u = url.trim();
      if (!u) return;
      analyze.mutate({ url: u, metadata: buildMeta() });
    },
    [analyze, url, buildMeta],
  );

  const setMetaField = (k: keyof AnalyzeMetadata) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setMeta((m) => ({ ...m, [k]: e.target.value }));

  return (
    <div className="min-h-screen">
      <header className="glass sticky top-0 z-20 flex h-16 items-center justify-between border-b px-4 lg:px-8">
        <Link href="/" aria-label={t("analyze.backHome")}>
          <Logo />
        </Link>
        <ThemeToggle />
      </header>

      <main className="mx-auto w-full max-w-2xl px-4 py-10 lg:py-14">
        <div className="mb-6 space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">{t("analyze.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("analyze.subtitle")}</p>
        </div>

        <form onSubmit={onSubmit} className="space-y-3">
          <div>
            <label htmlFor="analyze-url" className="mb-1.5 block text-sm font-medium">
              {t("analyze.input.label")}
            </label>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Link2
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                  aria-hidden
                />
                <Input
                  id="analyze-url"
                  // A plain text field on purpose: the ENGINE is the source of truth for validity
                  // (it returns status "invalid_url"), so we let any string submit and render our
                  // own localized guidance rather than the browser's native `type=url` bubble.
                  type="text"
                  inputMode="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder={t("analyze.input.placeholder")}
                  className="pl-9"
                  autoComplete="off"
                  autoFocus
                />
              </div>
              <Button type="submit" disabled={!url.trim() || analyze.isPending}>
                {analyze.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <ScanSearch className="h-4 w-4" aria-hidden />
                )}
                {t("analyze.submit")}
              </Button>
            </div>
          </div>

          {/* Optional page details — improves fetchless scoring when the article isn't in the catalog. */}
          <div>
            <button
              type="button"
              onClick={() => setShowMeta((v) => !v)}
              aria-expanded={showMeta}
              className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
            >
              <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", showMeta && "rotate-180")} aria-hidden />
              {t("analyze.metadata.toggle")}
            </button>
            {showMeta && (
              <div className="mt-3 space-y-3 rounded-lg border bg-card/40 p-4">
                <p className="text-xs text-muted-foreground">{t("analyze.metadata.hint")}</p>
                <MetaField id="meta-title" label={t("analyze.metadata.title")} value={meta.title ?? ""} onChange={setMetaField("title")} />
                <MetaField id="meta-desc" label={t("analyze.metadata.description")} value={meta.description ?? ""} onChange={setMetaField("description")} />
                <MetaField id="meta-outlet" label={t("analyze.metadata.outlet")} value={meta.outlet ?? ""} onChange={setMetaField("outlet")} />
                <MetaField id="meta-category" label={t("analyze.metadata.category")} value={meta.category ?? ""} onChange={setMetaField("category")} />
              </div>
            )}
          </div>
        </form>

        <section className="mt-8" aria-busy={analyze.isPending}>
          {analyze.isPending ? (
            <div className="space-y-3">
              <span className="sr-only" role="status">
                {t("analyze.loading")}
              </span>
              <Skeleton className="h-6 w-40" />
              <Skeleton className="h-28 w-full" />
              <Skeleton className="h-28 w-full" />
            </div>
          ) : analyze.isError ? (
            <ErrorState message={t("analyze.error")} onRetry={() => onSubmit(new Event("submit") as unknown as React.FormEvent)} />
          ) : analyze.data ? (
            <AnalysisResult result={analyze.data} />
          ) : (
            <EmptyState icon={ScanSearch} title={t("analyze.empty.title")} description={t("analyze.empty.body")} />
          )}
        </section>
      </main>
    </div>
  );
}

function MetaField({
  id,
  label,
  value,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <Input id={id} value={value} onChange={onChange} autoComplete="off" />
    </div>
  );
}
