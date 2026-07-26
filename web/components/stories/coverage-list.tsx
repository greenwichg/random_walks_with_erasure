"use client";

import * as React from "react";
import type { LeanBucket, Register, StoryCoverage } from "@/types/domain";
import { SectionHeader } from "@/components/shared/section-header";
import { LeanBadge, RegisterBadge } from "@/components/shared/article-badges";
import { ReadArticleButton } from "@/components/shared/read-article-button";
import { SaveButton } from "@/components/shared/save-button";
import { Button } from "@/components/ui/button";
import { FilterChip } from "@/components/ui/filter-chip";
import { useTranslation } from "@/lib/i18n";

const LEAN_FILTERS: ("all" | LeanBucket)[] = ["all", "left", "center", "right"];

/**
 * The story's article coverage as a FILTERABLE list — the "how is it covered, and by whom" section.
 *
 * Every filter is backed by a field the coverage rows actually carry: political lean
 * (left/center/right), register (reporting/opinion/mixed — offered only when present), and
 * publication order. Nothing here fabricates facets: there is no factuality/ownership/geography
 * data in the contract, so there are no such filters.
 *
 * Rows follow the home page's list language (dense hover rows in a divide-y run, not a stack of
 * shadowed cards): provenance line → headline → actions. Read/Save reuse the existing pipeline
 * components, so recording a read here is byte-identical to every other surface.
 */
export function CoverageList({ coverage }: { coverage: StoryCoverage[] }) {
  const { t, timeAgo, formatCompact } = useTranslation();
  const [lean, setLean] = React.useState<"all" | LeanBucket>("all");
  const [register, setRegister] = React.useState<"all" | Register>("all");
  const [oldestFirst, setOldestFirst] = React.useState(false);

  const leanCounts = React.useMemo(() => {
    const counts: Record<string, number> = { left: 0, center: 0, right: 0 };
    for (const row of coverage) if (row.leanBucket) counts[row.leanBucket] = (counts[row.leanBucket] ?? 0) + 1;
    return counts;
  }, [coverage]);

  /** Registers actually present in this cluster — a filter for a value with zero rows is noise. */
  const registers = React.useMemo(() => {
    const present = new Set<Register>();
    for (const row of coverage) if (row.register) present.add(row.register);
    return (["reporting", "opinion", "mixed"] as Register[]).filter((r) => present.has(r));
  }, [coverage]);

  const rows = React.useMemo(() => {
    const filtered = coverage.filter(
      (row) =>
        (lean === "all" || row.leanBucket === lean) &&
        (register === "all" || row.register === register),
    );
    return filtered.sort((a, b) =>
      oldestFirst
        ? (a.publishedAt ?? "").localeCompare(b.publishedAt ?? "")
        : (b.publishedAt ?? "").localeCompare(a.publishedAt ?? ""),
    );
  }, [coverage, lean, register, oldestFirst]);

  const reset = () => {
    setLean("all");
    setRegister("all");
  };

  return (
    <section aria-labelledby="coverage-list-heading">
      <SectionHeader id="coverage-list-heading" title={t("stories.coverageAcross")} />

      <div
        role="toolbar"
        aria-label={t("stories.coverageAcross")}
        className="mb-2 flex flex-wrap items-center gap-2"
      >
        {LEAN_FILTERS.map((value) => (
          <FilterChip
            key={value}
            active={lean === value}
            onClick={() => setLean(value)}
            label={value === "all" ? t("rec.filter.all") : t(`filter.${value}`)}
            count={value === "all" ? coverage.length : leanCounts[value]}
          />
        ))}

        {registers.length > 1 && (
          <>
            <span aria-hidden className="h-4 w-px bg-border" />
            {registers.map((value) => (
              <FilterChip
                key={value}
                active={register === value}
                onClick={() => setRegister(register === value ? "all" : value)}
                label={t(`register.${value}`)}
              />
            ))}
          </>
        )}

        <span aria-hidden className="h-4 w-px bg-border" />
        <FilterChip
          active={false}
          onClick={() => setOldestFirst((v) => !v)}
          label={oldestFirst ? t("filter.oldest") : t("filter.newest")}
        />
      </div>

      {rows.length === 0 ? (
        <div className="rounded-lg border border-dashed bg-card/40 px-4 py-8 text-center">
          <p className="text-sm text-muted-foreground">{t("story.noMatches")}</p>
          <Button variant="outline" size="sm" className="mt-3" onClick={reset}>
            {t("common.reset")}
          </Button>
        </div>
      ) : (
        <ul className="divide-y">
          {rows.map((row, i) => (
            <li key={`${row.publisher}-${row.publishedAt}-${i}`} className="group">
              <div className="-mx-2 flex flex-col gap-3 rounded-md px-2 py-3 transition-colors hover:bg-accent/40 sm:flex-row sm:items-center">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                    <span className="font-medium text-foreground">{row.publisher}</span>
                    <LeanBadge lean={row.lean} bucket={row.leanBucket} />
                    {row.register && <RegisterBadge register={row.register} />}
                    {row.publishedAt && <span>{timeAgo(row.publishedAt)}</span>}
                  </div>
                  <h3 className="mt-1 line-clamp-2 text-sm font-semibold leading-snug tracking-tight">
                    {row.headline}
                  </h3>
                </div>
                <div className="flex shrink-0 items-center gap-2 self-start sm:self-center">
                  <ReadArticleButton article={{ url: row.url, headline: row.headline }} openedFrom="stories" />
                  {row.url && (
                    <SaveButton
                      article={{
                        id: row.url,
                        url: row.url,
                        headline: row.headline,
                        publisher: row.publisher,
                        publishedAt: row.publishedAt,
                      }}
                    />
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-xs text-muted-foreground">{formatCompact(rows.length)} / {formatCompact(coverage.length)}</p>
    </section>
  );
}

