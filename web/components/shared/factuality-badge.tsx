"use client";

import { ExternalLink } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import { Badge } from "@/components/ui/badge";
import type { PublisherProfile } from "@ih/core/domain/types";

/**
 * A rater's factuality verdict, shown with its attribution rather than as our own claim.
 *
 * Three rules, each one a thing the component must NOT do:
 *
 *  * It never renders a value without saying who issued it and when. The verdict is a third
 *    party's assessment of a named news organisation; presented bare it reads as ours, and read
 *    a year from now it would assert that the rater still says it. So the source and the
 *    retrieval date sit beside the label in the same breath, not behind a tooltip.
 *  * It never invents a level for an unrated outlet. Unknown renders as an explicit "not rated",
 *    the same treatment the lean already gets (L2.2) — absence stated, never a middle value and
 *    never a silently missing row that reads as "fine".
 *  * It never paraphrases. The label is the rater's own vocabulary ("Mostly Factual" is a mild
 *    reservation, "Mixed" a serious one) because collapsing them tells the reader something the
 *    rater did not say.
 *
 * The link goes to the rater's own listing for this outlet, so a reader can check what it says
 * TODAY against what we recorded on `asOf`.
 */
/** Day precision: the date says how stale the verdict is, and a rater's revision cadence is
 *  months. A time of day would imply a freshness the record does not have. */
const DATE_OPTS: Intl.DateTimeFormatOptions = { year: "numeric", month: "short", day: "numeric" };

export function FactualityBadge({ factuality }: { factuality?: PublisherProfile["factuality"] }) {
  const { t, formatDate } = useTranslation();

  if (!factuality) {
    return (
      <Badge variant="outline" className="text-muted-foreground">
        {t("publishers.factuality.notRated")}
      </Badge>
    );
  }

  const { value, source, asOf, ratingUrl } = factuality;
  const level = t(`publishers.factuality.level.${value}`);
  const sourceName = t(`publishers.factuality.source.${source}`);

  return (
    <span className="inline-flex items-center gap-1.5">
      <Badge variant="secondary">{t("publishers.factuality.value", { level })}</Badge>
      <a
        href={ratingUrl}
        target="_blank"
        rel="noopener noreferrer nofollow"
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground hover:underline"
        // The accessible name carries the whole claim, so a screen-reader user gets the
        // attribution and the date rather than an unlabelled "link".
        aria-label={t("publishers.factuality.attribution.full", {
          level,
          source: sourceName,
          date: formatDate(asOf, DATE_OPTS),
        })}
      >
        <span aria-hidden="true">
          {t("publishers.factuality.attribution", { source: sourceName, date: formatDate(asOf, DATE_OPTS) })}
        </span>
        <ExternalLink className="h-3 w-3" aria-hidden="true" />
      </a>
    </span>
  );
}
