"use client";

import Link from "next/link";
import { Quote } from "lucide-react";
import type { StoryCoverage } from "@ih/core/domain/types";
import { framingComparison } from "@ih/core/logic/framing";
import { SectionHeader } from "@/components/shared/section-header";
import { ReadArticleButton } from "@/components/shared/read-article-button";
import { LEAN_META } from "@ih/core/logic/metrics";
import { useTranslation } from "@/lib/i18n";

/**
 * "How each side frames it" — the same event's headline from each rated side, next to each other.
 *
 * The CoverageList below lets a reader inspect one side at a time; this module is the juxtaposition
 * itself, which is the thing a filter can never show. Derivation (side selection, representative
 * headline, honesty gates) lives in lib/framing.ts and is unit-tested; this component only renders
 * what the derivation returns and disappears entirely when it returns null — a one-sided story is
 * the blindspot banner's job, not a fake comparison.
 *
 * The side is encoded twice (colour rail + text label), matching the blindspot module's rule that
 * meaning never depends on colour alone. Reads recorded here go through the same ReadArticleButton
 * pipeline as every other surface.
 */
export function FramingComparison({ coverage }: { coverage: StoryCoverage[] }) {
  const { t, timeAgo } = useTranslation();
  const sides = framingComparison(coverage);
  if (!sides) return null;

  return (
    <section aria-labelledby="framing-heading">
      <SectionHeader
        id="framing-heading"
        title={t("stories.framing.title")}
        eyebrow={t("stories.framing.eyebrow")}
      />
      <ul className={`grid gap-3 sm:grid-cols-2 ${sides.length === 3 ? "lg:grid-cols-3" : ""}`}>
        {sides.map(({ side, row, count }) => {
          const meta = LEAN_META[side];
          return (
            <li key={side} className="flex gap-3 rounded-md border bg-card p-4">
              <span aria-hidden className="w-1 shrink-0 rounded-full" style={{ background: meta.color }} />
              <div className="flex min-w-0 flex-1 flex-col">
                <p
                  className="mb-1 flex items-center justify-between gap-2 text-[0.68rem] font-semibold uppercase tracking-wide"
                  style={{ color: meta.color }}
                >
                  <span>{t(`filter.${side}`)}</span>
                  <span className="font-normal normal-case tracking-normal text-muted-foreground">
                    {t(count === 1 ? "stories.framing.sources.one" : "stories.framing.sources.other", { n: count })}
                  </span>
                </p>
                <blockquote className="mb-2 flex-1 text-sm font-semibold leading-snug tracking-tight">
                  <Quote aria-hidden className="mb-1 h-3 w-3 text-muted-foreground" />
                  {row.headline}
                </blockquote>
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                  <Link
                    href={`/publishers/${encodeURIComponent(row.publisher)}`}
                    className="font-medium text-foreground hover:text-primary hover:underline"
                  >
                    {row.publisher}
                  </Link>
                  <span aria-hidden>·</span>
                  <time dateTime={row.publishedAt}>{timeAgo(row.publishedAt)}</time>
                  {row.register && row.register !== "reporting" && (
                    <>
                      <span aria-hidden>·</span>
                      <span>{t(`register.${row.register}`)}</span>
                    </>
                  )}
                </div>
                {row.url && (
                  <ReadArticleButton
                    article={{ url: row.url, headline: row.headline }}
                    openedFrom="stories"
                    className="mt-2 self-start"
                  />
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
