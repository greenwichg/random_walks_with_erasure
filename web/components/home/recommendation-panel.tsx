"use client";

import Link from "next/link";
import { ArrowLeftRight, Sparkles } from "lucide-react";
import type { Recommendation } from "@ih/core/domain/types";
import { SectionHeader } from "@/components/shared/section-header";
import { useTranslation } from "@/lib/i18n";

/**
 * "Picked for you" — a compact preview of the reader's real recommendation feed.
 *
 * Two deliberate constraints:
 *  - it reuses `localizeExplanation`, the SAME structured-explanation path the full recommendation
 *    card uses, so the "why" here can never disagree with (or fall back to English behind) the
 *    Recommendations page;
 *  - rows link to `/recommendations` rather than opening the publisher directly. Opening an
 *    article is what records a read and feeds Open-Mindedness; that pipeline lives in
 *    `ReadArticleButton` on the real surface, and duplicating it in a preview panel would risk two
 *    divergent read paths. The panel previews and hands off.
 */
export function RecommendationPanel({ recs, limit = 4 }: { recs: Recommendation[]; limit?: number }) {
  const { t, localizeExplanation } = useTranslation();
  const items = recs.slice(0, limit);

  return (
    <section aria-labelledby="recs-heading" className="rounded-lg border bg-card p-4">
      <SectionHeader
        id="recs-heading"
        title={t("home.recs.title")}
        href="/recommendations"
        actionLabel={t("home.viewAll")}
        className="mb-3"
      />

      {items.length === 0 ? (
        <p className="rounded-lg border border-dashed bg-card/40 px-4 py-6 text-center text-sm text-muted-foreground">
          {t("home.recs.empty")}
        </p>
      ) : (
        <ul className="divide-y">
          {items.map((rec) => {
            const why = localizeExplanation(rec.explanation ?? { message: rec.reason });
            return (
              <li key={rec.article.id}>
                <Link
                  href="/recommendations"
                  className="group block rounded-md py-3 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                >
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <span className="text-xs font-medium text-muted-foreground">
                      {rec.article.publisher}
                    </span>
                    {rec.crossCutting && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[0.68rem] font-medium text-primary">
                        <ArrowLeftRight className="h-3 w-3" aria-hidden />
                        {t("home.recs.cross")}
                      </span>
                    )}
                  </div>

                  <h3 className="line-clamp-2 text-sm font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary">
                    {rec.article.headline}
                  </h3>

                  {why && <p className="mt-1.5 line-clamp-2 text-xs text-muted-foreground">{why}</p>}

                  <p className="mt-1.5 inline-flex items-center gap-1 text-[0.68rem] font-medium text-primary/80">
                    <Sparkles className="h-3 w-3" aria-hidden />
                    {t("rec.helps", { metric: t(`metric.${rec.helpsMetric}.label`) })}
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
