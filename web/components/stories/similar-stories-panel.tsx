"use client";

import * as React from "react";
import Link from "next/link";
import { EyeOff, RefreshCw, Sparkles } from "lucide-react";
import type { Story } from "@ih/core/domain/types";
import { SectionHeader } from "@/components/shared/section-header";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useTranslation } from "@/lib/i18n";

/**
 * "Similar Stories" — the story page's answer to "what else covers this", in the shell
 * "Picked for you" (recommendation-panel.tsx) established: same container, same `SectionHeader`
 * with its trailing action, same `divide-y` rows, same four-line row (eyebrow · headline ·
 * supporting line · indicator).
 *
 * IT COMPUTES NOTHING ABOUT SIMILARITY. The stories are the engine's own ranked answer
 * (`/api/stories/{id}/similar`), fetched once by the story page and handed here — no second
 * opinion. That constraint is the whole reason this file is presentational: a card that decided
 * for itself what "similar" meant would be a second matching rule to disagree with the first, and
 * this feature's history is a long argument for having exactly one.
 *
 * What it DOES decide is the supporting line, and that is presentation over data the story already
 * carries: the topics this story and the current one have in common (`story_tags`), which is the
 * honest answer to "why am I being shown this" — it names the actual overlap rather than asserting
 * a relevance the reader cannot check. Where two stories share no NAMED topic — the engine can rank
 * on profile overlap alone — it falls back to the coverage count, which is the next most useful
 * thing about a cluster.
 *
 * IT SAYS SO RATHER THAN VANISHING when there is nothing, and that discipline is inherited, not
 * invented: it belonged to the horizontal rail this card replaced, and outlived it because the
 * reason for it did. An empty result used to render `null`, so the whole section disappeared — and
 * when a bad similarity threshold emptied it on every story, the page simply ended, with nothing to
 * say it had. A reader cannot tell a deliberate silence from a broken feature by looking at a gap.
 * Three outcomes, three different renders:
 *
 *   in flight   skeleton rows, so the card holds its height instead of appearing late
 *   failed      "couldn't be loaded", with a retry — never "nothing is similar", which is a lie a
 *               failed request cannot support
 *   empty       one line saying nothing else covers this event, and that the space is left empty
 *               on purpose
 *
 * The three are passed in rather than inferred from `similar.length`, because an empty array is
 * what loading, failure and genuine absence all look like from here.
 */

/** How many stories the page fetches. Ten, because the ranked answer costs the same at four and
 *  the reader who presses "View all" gets a real list rather than one more row. */
export const MAX_CARDS = 10;

/** Rows shown before "View all". Four, like the reference: enough to be worth a look, short enough
 *  that the card stays vertically balanced beside the coverage list. */
export const PANEL_CARDS = 4;

export function SimilarStoriesPanel({
  story,
  similar,
  limit = PANEL_CARDS,
  isLoading = false,
  isError = false,
  onRetry,
}: {
  /** The story being read — supplies the topics a row's overlap is measured against. */
  story: Story;
  /** The engine's ranked answer. */
  similar: Story[];
  limit?: number;
  /** The query is in flight. Renders skeleton rows, never the empty line. */
  isLoading?: boolean;
  /** The query failed. Renders the retry notice, never the empty line. */
  isError?: boolean;
  onRetry?: () => void;
}) {
  const { t, formatCompact } = useTranslation();
  const [expanded, setExpanded] = React.useState(false);
  const items = expanded ? similar : similar.slice(0, limit);
  const mine = new Set((story.tags ?? []).map((tag) => tag.name));
  // "View all" is offered only when there IS more — a control that reveals nothing is a promise
  // the card cannot keep. It reveals the rest of THIS list in place: the ranked set is already
  // here, and there is no page of similar stories to send anyone to.
  const more = !isLoading && !isError && similar.length > limit;

  return (
    <section aria-labelledby="similar-panel-heading" className="rounded-lg border bg-card p-4">
      <SectionHeader
        id="similar-panel-heading"
        title={t("story.related")}
        onAction={more ? () => setExpanded((v) => !v) : undefined}
        actionLabel={more ? t(expanded ? "story.similar.less" : "home.viewAll") : undefined}
        className="mb-3"
      />

      {isLoading ? (
        <div className="divide-y" aria-hidden>
          {Array.from({ length: limit }).map((_, i) => (
            <div key={i} className="space-y-2 py-3">
              <Skeleton className="h-3 w-24" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-3 w-4/5" />
            </div>
          ))}
        </div>
      ) : isError ? (
        <div className="flex flex-col items-start gap-3 py-2">
          <p className="text-sm text-muted-foreground">{t("story.similar.error")}</p>
          {onRetry && (
            <Button variant="outline" size="sm" onClick={onRetry}>
              <RefreshCw className="h-4 w-4" aria-hidden /> {t("common.tryAgain")}
            </Button>
          )}
        </div>
      ) : items.length === 0 ? (
        <p className="rounded-lg border border-dashed bg-card/40 px-4 py-6 text-center text-sm text-muted-foreground">
          {t("story.similar.none")}
        </p>
      ) : (
        <ul className="divide-y">
          {items.map((item) => {
            // Named overlap with the story being read. Labels rather than keys, and the engine's
            // own order, so the two most relevant shared topics lead.
            const shared = (item.tags ?? []).filter((tag) => mine.has(tag.name)).slice(0, 2);
            const publisher = item.coverage?.[0]?.publisher ?? item.publishers?.[0];
            return (
              <li key={item.id}>
                <Link
                  href={`/stories/${item.id}`}
                  className="group block rounded-md py-3 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                >
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    {publisher && (
                      <span className="text-xs font-medium text-muted-foreground">{publisher}</span>
                    )}
                    {/* The reference's "Other side" chip, carrying the fact this page actually
                        holds about another story: a DETECTED coverage gap. Absent when the story
                        is balanced or unrated — a gap is a counted finding, never a default.
                        The label is the full sentence the rest of the product uses ("Thin coverage
                        on the right"), not the bare side: a chip reading "Right" beside a headline
                        says the story leans right, which is the opposite of what was measured. */}
                    {item.blindspotSide && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[0.68rem] font-medium text-primary">
                        <EyeOff className="h-3 w-3" aria-hidden />
                        {t("stories.thinCoverage", {
                          side: t(`filter.${item.blindspotSide}`).toLowerCase(),
                        })}
                      </span>
                    )}
                  </div>

                  <h3 className="line-clamp-2 text-sm font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary">
                    {item.title}
                  </h3>

                  {item.summary && (
                    <p className="mt-1.5 line-clamp-2 text-xs text-muted-foreground">{item.summary}</p>
                  )}

                  <p className="mt-1.5 inline-flex items-center gap-1 text-[0.68rem] font-medium text-primary/80">
                    <Sparkles className="h-3 w-3" aria-hidden />
                    {shared.length > 0
                      ? t("story.similar.alsoAbout", {
                          topics: shared.map((tag) => tag.label).join(", "),
                        })
                      : t("storyCard.sources", { n: formatCompact(item.totalCoverage) })}
                  </p>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
