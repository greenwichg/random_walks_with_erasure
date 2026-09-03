"use client";

import type { LeanBucket, ViewpointDistribution } from "@ih/core/domain/types";
import { LEAN_META } from "@ih/core/logic/metrics";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const SIDES: readonly LeanBucket[] = ["left", "center", "right"];

/** Shares as whole percents that sum to 100, plus the side carrying the most coverage. */
export function biasShares(d: ViewpointDistribution | undefined) {
  const total = (d?.left ?? 0) + (d?.center ?? 0) + (d?.right ?? 0);
  if (total <= 0) return null;
  const pct = (s: LeanBucket) => Math.round(((d?.[s] ?? 0) / total) * 100);
  const shares = { left: pct("left"), center: pct("center"), right: pct("right") };
  const top = SIDES.reduce((a, b) => (shares[b] > shares[a] ? b : a));
  return { shares, top };
}

/**
 * The desktop front page's coverage strip — the reference layout's one data mark on every story:
 * a thin three-segment bar (left / centre / right, hairline gaps, square ends) and, in list rows,
 * one caption under it naming the dominant side and the source count. The lead and the topic
 * cards use `labels` instead, printing the share inside each segment.
 *
 * Same distribution the SpectrumBar and coverage plate draw from; only the rendering is denser.
 * The bar is decorative — the caption or labels carry the numbers as text.
 */
export function BiasStrip({
  distribution,
  sources,
  labels = false,
  className,
}: {
  distribution: ViewpointDistribution | undefined;
  /** Article count, for the "N% Centre coverage: N sources" caption. Omit for a bare bar. */
  sources?: number;
  /** Print "Left 51%" etc. inside the segments (lead / feature size). */
  labels?: boolean;
  className?: string;
}) {
  const { t, formatCompact } = useTranslation();
  const shares = biasShares(distribution);
  if (!shares) return null;
  const { shares: s, top } = shares;

  return (
    <div className={cn("min-w-0", className)}>
      <div aria-hidden className={cn("flex w-full gap-px overflow-hidden rounded-[2px]", labels ? "h-[18px]" : "h-[5px]")}>
        {SIDES.map((side) => {
          const pct = s[side];
          if (pct <= 0) return null;
          return (
            <div
              key={side}
              className="flex items-center justify-center overflow-hidden"
              style={{ flexGrow: pct, flexBasis: 0, background: LEAN_META[side].color }}
            >
              {labels && pct >= 12 && (
                <span className="truncate px-1 text-[10px] font-semibold tabular-nums text-[hsl(var(--card))]">
                  {pct >= 22 ? t(`filter.${side}`) : t(`filter.${side}`).charAt(0)} {pct}%
                </span>
              )}
            </div>
          );
        })}
      </div>
      {labels && (
        <span className="sr-only">
          {SIDES.map((side) => `${t(`filter.${side}`)} ${s[side]}%`).join(" · ")}
        </span>
      )}
      {!labels && sources != null && (
        <p className="mt-1 text-[11px] leading-tight text-muted-foreground">
          {t("storyCard.coverageCaption", {
            pct: s[top],
            side: t(`filter.${top}`),
            n: formatCompact(sources),
          })}
        </p>
      )}
    </div>
  );
}
