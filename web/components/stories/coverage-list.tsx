"use client";

import * as React from "react";
import Link from "next/link";
import { ChevronDown } from "lucide-react";
import type { LeanBucket, Register, StoryCoverage } from "@ih/core/domain/types";
import { splitCoverage } from "@ih/core/logic/story-attached";
import { collapseConsecutive } from "@ih/core/logic/coverage-groups";
import { hostIconCandidates } from "@ih/core/logic/publisher-logo";
import { monogram } from "@ih/core/logic/placeholder-art";
import { SectionHeader } from "@/components/shared/section-header";
import { LeanBadge } from "@/components/shared/article-badges";
import { PublisherLogo } from "@/components/shared/publisher-logo";
import { ContinuationStrip } from "@/components/shared/continuation-strip";
import { ReadArticleButton } from "@/components/shared/read-article-button";
import { SaveButton } from "@/components/shared/save-button";
import { Button } from "@/components/ui/button";
import { FilterChip } from "@/components/ui/filter-chip";
import { useTranslation } from "@/lib/i18n";

const LEAN_FILTERS: ("all" | LeanBucket)[] = ["all", "left", "center", "right"];

// Rows shown before "Read more". Six is an editorial choice, not a performance one: the list is
// the page's tallest section, and a reader deciding whether to go deeper needs a legible sample,
// not the whole cluster. The catalog median story is 2 articles and p90 is 7, so most stories are
// already whole at first paint and never render the button at all.
const INITIAL = 6;
// What one "Read more" reveals. Deliberately larger than INITIAL: past the first click the reader
// has asked for the full list, so the batch exists only to keep the long tail off a single mount —
// the largest measured cluster is 318 rows, each mounting a Read and a Save control. Every story up
// to 46 rows opens completely on one click; beyond that the button simply returns.
const STEP = 40;

/**
 * The story's article coverage as a FILTERABLE list — the "how is it covered, and by whom" section,
 * on the Ground News comparison's reading rhythm: WHO first (publisher mark + name, with the lean
 * badge as its counterweight), the headline as the row's one big line, then a quiet metadata line
 * whose actions stay ghost-subtle (hover-revealed on desktop, always present on touch).
 *
 * Every filter is backed by a field the coverage rows actually carry: political lean
 * (left/center/right), register (reporting/opinion/mixed — offered only when present, and worn as
 * plain text in the meta line, not a pill), and publication order. Nothing here fabricates facets.
 *
 * Repetition is grouped where it actually occurs: an outlet filing several updates IN A ROW (the
 * liveblog cadence) collapses to its newest row plus a "+N earlier" expander — consecutive runs
 * only, so the chronological order the sort promises is never reshuffled (coverage-groups.ts).
 * Literal reposts were never rows here at all: ingest dedupes by canonical URL.
 *
 * Attached Tier B rows (M4) render as their own labeled group BELOW the panel rows — "from beyond
 * the panel": outlets we carry but whose articles never voted in this story. They stay out of the
 * filter counts and the N/M line (those describe the panel), and the group only appears with the
 * filters at rest — an attached row carries no lean, so a lean chip that surfaced it would be
 * fabricating the very fact the row honestly lacks.
 */
export function CoverageList({ coverage }: { coverage: StoryCoverage[] }) {
  const { t, timeAgo, formatCompact } = useTranslation();
  const [lean, setLean] = React.useState<"all" | LeanBucket>("all");
  const [register, setRegister] = React.useState<"all" | Register>("all");
  const [oldestFirst, setOldestFirst] = React.useState(false);

  const { panel, attached } = React.useMemo(() => splitCoverage(coverage), [coverage]);

  const leanCounts = React.useMemo(() => {
    const counts: Record<string, number> = { left: 0, center: 0, right: 0 };
    for (const row of panel) if (row.leanBucket) counts[row.leanBucket] = (counts[row.leanBucket] ?? 0) + 1;
    return counts;
  }, [panel]);

  /** Registers actually present in this cluster — a filter for a value with zero rows is noise. */
  const registers = React.useMemo(() => {
    const present = new Set<Register>();
    for (const row of panel) if (row.register) present.add(row.register);
    return (["reporting", "opinion", "mixed"] as Register[]).filter((r) => present.has(r));
  }, [panel]);

  const rows = React.useMemo(() => {
    const filtered = panel.filter(
      (row) =>
        (lean === "all" || row.leanBucket === lean) &&
        (register === "all" || row.register === register),
    );
    return filtered.sort((a, b) =>
      oldestFirst
        ? (a.publishedAt ?? "").localeCompare(b.publishedAt ?? "")
        : (b.publishedAt ?? "").localeCompare(a.publishedAt ?? ""),
    );
  }, [panel, lean, register, oldestFirst]);

  const groups = React.useMemo(() => collapseConsecutive(rows), [rows]);

  const attachedRows = React.useMemo(
    () =>
      [...attached].sort((a, b) =>
        oldestFirst
          ? (a.publishedAt ?? "").localeCompare(b.publishedAt ?? "")
          : (b.publishedAt ?? "").localeCompare(a.publishedAt ?? ""),
      ),
    [attached, oldestFirst],
  );
  const showAttached = attachedRows.length > 0 && lean === "all" && register === "all";

  // Any filter or order change starts the batch AND the expanded runs over — otherwise "Load more"
  // would carry a previous filter's depth into a shorter result set, and an expander would stay
  // open on a group that no longer holds the same rows.
  const [visible, setVisible] = React.useState(INITIAL);
  const [openRuns, setOpenRuns] = React.useState<Set<string>>(() => new Set());
  React.useEffect(() => {
    setVisible(INITIAL);
    setOpenRuns(new Set());
  }, [lean, register, oldestFirst]);

  const shown = groups.slice(0, visible);
  const hasMore = visible < groups.length;

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
        className="mb-3 flex flex-wrap items-center gap-2"
      >
        {LEAN_FILTERS.map((value) => (
          <FilterChip
            key={value}
            active={lean === value}
            onClick={() => setLean(value)}
            label={value === "all" ? t("rec.filter.all") : t(`filter.${value}`)}
            count={value === "all" ? panel.length : leanCounts[value]}
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
          {shown.map((group, i) => {
            const runKey = `${group.lead.publisher}|${group.lead.publishedAt}`;
            const open = openRuns.has(runKey);
            return (
              <li key={`${runKey}-${i}`} className="py-1">
                <CoverageRow row={group.lead} badge={<LeanBadge lean={group.lead.lean} bucket={group.lead.leanBucket} />} />
                {group.rest.length > 0 && !open && (
                  <button
                    type="button"
                    onClick={() => setOpenRuns((s) => new Set(s).add(runKey))}
                    className="mb-2 inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  >
                    <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                    {t("story.moreFrom", { n: formatCompact(group.rest.length), publisher: group.lead.publisher })}
                  </button>
                )}
                {open &&
                  group.rest.map((row, j) => (
                    <div key={`${row.publishedAt}-${j}`} className="border-l-2 pl-3">
                      <CoverageRow row={row} badge={<LeanBadge lean={row.lean} bucket={row.leanBucket} />} />
                    </div>
                  ))}
              </li>
            );
          })}
        </ul>
      )}
      {hasMore && (
        <div className="mt-4 flex justify-center">
          <Button
            variant="outline"
            className="w-full gap-1.5 sm:w-auto sm:min-w-56"
            onClick={() => setVisible((v) => v + STEP)}
          >
            {t("story.readMore", { n: formatCompact(groups.length - visible) })}
            <ChevronDown className="h-4 w-4" aria-hidden />
          </Button>
        </div>
      )}
      <p className="mt-2 text-xs text-muted-foreground">{formatCompact(rows.length)} / {formatCompact(panel.length)}</p>

      {/* Attached Tier B coverage — the addendum the engine appended after the members. Its own
          labeled group, never mixed into the panel rows above: the divider IS the tier boundary,
          drawn where the data draws it. Dashed border + no lean badge = "carried, not rated". */}
      {showAttached && (
        <div className="mt-5 rounded-lg border border-dashed bg-card/40 px-4 py-3">
          <h3 className="text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground">
            {t("story.beyondPanel", { n: formatCompact(attachedRows.length) })}
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">{t("story.beyondPanelNote")}</p>
          <ul className="mt-1 divide-y">
            {attachedRows.map((row, i) => (
              <li key={`${row.publisher}-${row.publishedAt}-${i}`} className="py-1">
                <CoverageRow
                  row={row}
                  badge={
                    <span className="inline-flex items-center rounded-full border border-dashed px-2 py-0.5 text-[0.65rem] font-medium text-muted-foreground">
                      {t("story.beyondPanelBadge")}
                    </span>
                  }
                />
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

/**
 * One coverage row, GN-rhythm: publisher pill + its badge counterweight, the headline as the
 * dominant line, then one quiet meta line that also carries the actions — ghost-subtle, revealed
 * on hover/focus on desktop and always present on touch, where there is no hover to reveal them.
 * The pill's icon walks the same site-icon chain every logo surface uses (monogram terminal).
 */
function CoverageRow({ row, badge }: { row: StoryCoverage; badge: React.ReactNode }) {
  const { t, timeAgo } = useTranslation();
  const icons = hostIconCandidates(row.url);
  return (
    <div className="group">
      <div className="-mx-3 rounded-lg px-3 py-3.5 transition-colors hover:bg-accent/30">
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5">
          <Link
            href={`/publishers/${encodeURIComponent(row.publisher)}`}
            className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-muted/70 py-1 pl-1 pr-2.5 text-xs font-medium transition-colors hover:bg-muted"
          >
            <span className="grid h-[18px] w-[18px] shrink-0 place-items-center overflow-hidden rounded-full bg-card">
              <PublisherLogo
                logo={icons[0]}
                fallbacks={icons.slice(1)}
                sizePx={16}
                className="h-4 w-4"
                fallbackNode={
                  <span aria-hidden className="text-[0.5rem] font-bold text-muted-foreground">
                    {monogram(row.publisher)}
                  </span>
                }
              />
            </span>
            <span className="truncate">{row.publisher}</span>
          </Link>
          {badge}
        </div>

        <h3 className="mt-2 line-clamp-2 text-[0.95rem] font-semibold leading-snug tracking-tight">
          {row.headline}
        </h3>

        <div className="mt-1.5 flex min-h-7 items-center gap-2 text-xs text-muted-foreground">
          {row.publishedAt && <span>{timeAgo(row.publishedAt)}</span>}
          {row.register && (
            <>
              <span aria-hidden>·</span>
              <span>{t(`register.${row.register}`)}</span>
            </>
          )}
          <span className="flex-1" />
          <div className="flex shrink-0 items-center gap-1.5 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100 sm:group-focus-within:opacity-100">
            <ReadArticleButton
              article={{ url: row.url, headline: row.headline }}
              openedFrom="stories"
              variant="soft"
              className="h-7 px-2.5"
            />
            {row.url && (
              <SaveButton
                compact
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
      </div>

      {/* Story Continuation. This is the surface with the best odds by construction —
          every row here is already a cluster member, so the membership gate that rejects
          ~4 in 5 Discover cards passes automatically. The "all outlets" link is suppressed:
          it would point at this very page. */}
      {row.url ? <ContinuationStrip anchorUrl={row.url} showAllOutlets={false} surface="story" /> : null}
    </div>
  );
}
