"use client";

import * as React from "react";
import { EyeOff } from "lucide-react";
import type { LeanBucket, Story } from "@ih/core/domain/types";
import { PublisherLogo } from "@/components/shared/publisher-logo";
import { hostIconCandidates, logoCandidates } from "@ih/core/logic/publisher-logo";
import { monogram } from "@ih/core/logic/placeholder-art";
import { LEAN_META } from "@ih/core/logic/metrics";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const SIDES: readonly LeanBucket[] = ["left", "center", "right"];
/** Dominant-side lookup order — neutral first, so an even split never tints the plate partisan. */
const WASH_ORDER: readonly LeanBucket[] = ["center", "left", "right"];
const CARD_CHIPS = 4;
const MASTHEAD_CHIPS = 6;

/**
 * The COVERAGE PLATE — the designed no-image state for a story (docs/STORY_HERO_IMAGES.md and the
 * Ground News comparison behind it). It replaces the old bare coverage figure: same slot, same
 * counted facts, composed as an intentional object instead of a chart standing where a photo
 * should be. Everything on it is a fact of THIS story — kicker (topic/masthead label + time
 * span), publisher chips (each outlet's own site icons via the same fallback-walk rule every
 * logo surface uses, monogram terminal), the publisher-count credential, and the distribution
 * as one full-width labeled band. Never stock art, never a repeated decorative asset — the
 * repeated-image smell is exactly what the hero guard just removed from the imaged cards.
 *
 * Blindspot variant: when the story carries a detected gap, the plate STATES the finding
 * (thin side + its rated-source share) in place of the credential and tints toward the thin
 * side — the imageless card is the natural home for the claim, said once, not chip-and-plate
 * twice. A 0% side stays visibly present in the band as a hatched stub, never dropped (the
 * same "a missing side is VISIBLY missing" rule the old figure followed).
 *
 * `masthead` renders the 21:9 story-page variant, flush inside the article card where the hero
 * image would sit — before this, an imageless story PAGE had no designed state at all: the
 * hero self-hid and the page started abruptly at the topic label.
 */
export function CoveragePlate({
  story,
  masthead = false,
  className,
}: {
  story: Story;
  masthead?: boolean;
  className?: string;
}) {
  const { t, formatCompact } = useTranslation();

  const pubCount = story.publisherCount ?? story.publishers?.length ?? 0;
  const share = (s: LeanBucket) => Math.max(0, Math.min(1, story.distribution?.[s] ?? 0));
  const pct = (s: LeanBucket) => Math.round(share(s) * 100);
  const blind = story.blindspotSide;

  // First row per publisher, for chip icon derivation — coverage is newest-first, and any one of
  // the outlet's article hosts names the same site icons. When the detail payload carried a
  // server-resolved mark, that leads the walk; the host-derived guesses remain its fallbacks.
  const markOf = React.useMemo(() => {
    const m = new Map<string, string[]>();
    for (const c of story.coverage ?? []) {
      if (c.publisher && (c.url || c.publisherLogo) && !m.has(c.publisher)) {
        m.set(c.publisher, logoCandidates(c.publisherLogo, c.publisherLogoFallbacks ?? hostIconCandidates(c.url)));
      }
    }
    return m;
  }, [story.coverage]);
  const maxChips = masthead ? MASTHEAD_CHIPS : CARD_CHIPS;
  const chipNames = (story.publishers ?? []).slice(0, maxChips);
  const overflow = Math.max(0, pubCount - chipNames.length);

  const washSide: LeanBucket =
    blind ?? WASH_ORDER.reduce((a, b) => (share(b) > share(a) ? b : a));

  const spanH = Math.round(story.timeSpanHours ?? 0);
  const span =
    spanH >= 48
      ? t("storyCard.spanDays", { n: Math.round(spanH / 24) })
      : spanH >= 1
        ? t("storyCard.spanHours", { n: spanH })
        : "";
  const kicker = [masthead ? t("story.coverageMasthead") : story.topic, span]
    .filter(Boolean)
    .join(" · ");

  const counts = `${t("stories.publishers", { n: formatCompact(pubCount) })} · ${t(
    "stories.articlesCount",
    { n: formatCompact(story.totalCoverage) },
  )}`;
  const distLabel = SIDES.map((s) => `${t(`filter.${s}`)} ${pct(s)}%`).join(" · ");
  // One label carrying every fact on the plate; the visuals below are then decorative detail.
  const aria = `${
    blind
      ? `${t("storyCard.thinOn", { side: t(`filter.${blind}`).toLowerCase() })} — ${t(
          "storyCard.ratedShare",
          { pct: pct(blind) },
        )}. `
      : ""
  }${counts}. ${distLabel}`;

  return (
    <div
      role="img"
      aria-label={aria}
      className={cn(
        "flex flex-col justify-between gap-2 overflow-hidden",
        masthead
          ? "aspect-[21/9] w-full border-b bg-card px-5 py-4"
          : "mb-3 aspect-[16/9] rounded-lg border p-4",
        className,
      )}
      // Tinted by the story's own data — the thin side of a detected gap, else the dominant
      // side of the split. Token-derived, so both themes come for free.
      style={{
        backgroundImage: `linear-gradient(135deg, hsl(var(--${LEAN_META[washSide].token}) / ${
          blind ? 0.1 : 0.08
        }), transparent 70%)`,
      }}
    >
      <div className="flex min-w-0 items-start justify-between gap-2">
        {kicker && (
          <span className="truncate text-[0.68rem] font-semibold uppercase tracking-wider text-muted-foreground">
            {kicker}
          </span>
        )}
        {chipNames.length > 0 && (
          <ul aria-hidden className="flex shrink-0 items-center pl-1">
            {chipNames.map((p) => {
              const icons = markOf.get(p) ?? [];
              return (
                <li
                  key={p}
                  title={p}
                  className="-ml-2 grid h-6 w-6 place-items-center overflow-hidden rounded-full border-2 border-card bg-muted first:ml-0"
                >
                  <PublisherLogo
                    logo={icons[0]}
                    fallbacks={icons.slice(1)}
                    sizePx={20}
                    className="h-5 w-5"
                    fallbackNode={
                      <span className="text-[0.55rem] font-bold text-muted-foreground">
                        {monogram(p)}
                      </span>
                    }
                  />
                </li>
              );
            })}
            {overflow > 0 && (
              <li className="-ml-2 grid h-6 w-6 place-items-center rounded-full border-2 border-dashed border-border bg-card text-[0.55rem] font-semibold text-muted-foreground">
                +{formatCompact(overflow)}
              </li>
            )}
          </ul>
        )}
      </div>

      {blind ? (
        <div>
          <div
            className="flex items-center gap-1.5 text-sm font-semibold"
            style={{ color: LEAN_META[blind].color }}
          >
            <EyeOff className="h-4 w-4 shrink-0" aria-hidden />
            {t("storyCard.thinOn", { side: t(`filter.${blind}`).toLowerCase() })}
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {t("storyCard.ratedShare", { pct: pct(blind) })} ·{" "}
            {t("stories.publishers", { n: formatCompact(pubCount) })}
          </div>
        </div>
      ) : (
        <div className="flex items-baseline gap-2.5">
          <span
            className={cn(
              "font-bold leading-none tracking-tight tabular-nums",
              masthead ? "text-5xl" : "text-4xl",
            )}
          >
            {formatCompact(pubCount)}
          </span>
          <span className="text-xs leading-tight text-muted-foreground">
            {t("storyCard.publishersLabel")}
            <br />
            {t("stories.articlesCount", { n: formatCompact(story.totalCoverage) })}
          </span>
        </div>
      )}

      <div aria-hidden className={cn("flex gap-0.5 overflow-hidden rounded-md", masthead ? "h-8" : "h-7")}>
        {SIDES.map((s) => {
          const sh = share(s);
          const letter = t(`filter.${s}`).charAt(0);
          if (sh <= 0) {
            return (
              <div
                key={s}
                title={`${t(`filter.${s}`)} 0%`}
                className="grid min-w-[2.1rem] place-items-center"
                style={{
                  flexGrow: 0.06,
                  flexBasis: 0,
                  backgroundImage:
                    "repeating-linear-gradient(45deg, transparent 0 4px, hsl(var(--muted-foreground) / 0.18) 4px 6px)",
                }}
              >
                <span className="text-[0.66rem] font-semibold text-muted-foreground">
                  {letter} 0
                </span>
              </div>
            );
          }
          return (
            <div
              key={s}
              title={`${t(`filter.${s}`)} ${pct(s)}%`}
              className="grid place-items-center"
              style={{ flexGrow: sh, flexBasis: 0, background: LEAN_META[s].color }}
            >
              {sh >= 0.14 && (
                // Ink = the card token: white-on-hue in light, near-black-on-lightened-hue in
                // dark — contrast holds in both themes without a per-theme branch.
                <span className="text-[0.66rem] font-semibold tabular-nums text-[hsl(var(--card))]">
                  {letter} {pct(s)}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
