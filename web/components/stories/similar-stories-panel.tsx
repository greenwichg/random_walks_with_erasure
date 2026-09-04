"use client";

import Link from "next/link";
import { EyeOff, Sparkles } from "lucide-react";
import type { Story } from "@ih/core/domain/types";
import { SectionHeader } from "@/components/shared/section-header";
import { useTranslation } from "@/lib/i18n";

/**
 * "Similar Stories" — the rail card, in the shell "Picked for you" (recommendation-panel.tsx)
 * established: same container, same `SectionHeader` with its trailing action, same `divide-y`
 * rows, same four-line row (eyebrow · headline · supporting line · indicator).
 *
 * IT COMPUTES NOTHING ABOUT SIMILARITY. The stories are the engine's own ranked answer, fetched
 * once by the story page for the rail below and handed here — same request, same order, no second
 * opinion. That constraint is the whole reason this file is presentational: a card that decided
 * for itself what "similar" meant would be a second matching rule to disagree with the first, and
 * the rail's own history is a long argument for having exactly one.
 *
 * What it DOES decide is the supporting line, and that is presentation over data the story already
 * carries: the topics this story and the current one have in common (`story_tags`), which is the
 * honest answer to "why am I being shown this" — it names the actual overlap rather than asserting
 * a relevance the reader cannot check. Where two stories share no NAMED topic — the rail can rank
 * on profile overlap alone — it falls back to the coverage count, which is the next most useful
 * thing about a cluster.
 */

/** Rows in the card. Four, like the reference: enough to be worth a look, short enough that the
 *  rail stays vertically balanced beside the coverage list. The full ranked set is below. */
export const PANEL_CARDS = 4;

export function SimilarStoriesPanel({
  story,
  similar,
  limit = PANEL_CARDS,
}: {
  /** The story being read — supplies the topics a row's overlap is measured against. */
  story: Story;
  /** The engine's ranked answer, already fetched for the rail below. */
  similar: Story[];
  limit?: number;
}) {
  const { t, formatCompact } = useTranslation();
  const items = similar.slice(0, limit);
  const mine = new Set((story.tags ?? []).map((tag) => tag.name));

  return (
    <section aria-labelledby="similar-panel-heading" className="rounded-lg border bg-card p-4">
      <SectionHeader
        id="similar-panel-heading"
        title={t("story.related")}
        // The full ranked list is the rail at the foot of this page, so "View all" goes there
        // rather than inventing a destination. An in-page anchor is the honest target when the
        // complete answer is already on the page.
        href="#similar-stories-heading"
        actionLabel={t("home.viewAll")}
        className="mb-3"
      />

      {items.length === 0 ? (
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
