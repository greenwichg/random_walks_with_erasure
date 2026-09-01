"use client";

import * as React from "react";
import type { BiasGroups, OutletMark } from "@ih/core/logic/bias-distribution";
import { BIAS_BUCKETS, dominantBucket } from "@ih/core/logic/bias-distribution";
import { hostIconCandidates } from "@ih/core/logic/publisher-logo";
import { monogram } from "@ih/core/logic/placeholder-art";
import { LEAN_META } from "@ih/core/logic/metrics";
import { PublisherLogo } from "@/components/shared/publisher-logo";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { useTranslation } from "@/lib/i18n";

const COLUMN_CHIPS = 5;
const UNTRACKED_CHIPS = 8;

/**
 * The bias-distribution visual (Ground News comparison, adapted to the house system): the
 * headline share, the L/C/R spectrum counted in OUTLETS, one slim vertical capsule of outlet
 * marks per side, and the untracked strip for outlets the registry doesn't rate.
 *
 * Everything renders from one `groupOutletsByLean` result the panel computes — outlets, not
 * articles, so a wire service that filed nine pieces stands exactly once. A side with zero
 * outlets keeps its capsule as a hatched stub (the plate's "a missing side is VISIBLY
 * missing" rule); the panel states the same absence in words right below. Chips reuse the
 * site-icon fallback walk every logo surface uses, monogram terminal.
 */
export function BiasDistribution({ groups }: { groups: BiasGroups }) {
  const { t, formatCompact } = useTranslation();
  const dominant = dominantBucket(groups);
  if (groups.ratedCount === 0 && groups.untracked.length === 0) return null;

  return (
    <div>
      {dominant && (
        <p className="mb-2.5 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <span
            aria-hidden
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ background: LEAN_META[dominant.bucket].color }}
          />
          {t("story.biasSummary", { pct: dominant.pct, side: t(`filter.${dominant.bucket}`) })}
        </p>
      )}

      {groups.ratedCount > 0 && (
        <>
          <SpectrumBar
            distribution={{
              left: groups.buckets.left.length,
              center: groups.buckets.center.length,
              right: groups.buckets.right.length,
            }}
            height={10}
          />
          <div className="mt-3 grid grid-cols-3 gap-2">
            {BIAS_BUCKETS.map((bucket) => {
              const outlets = groups.buckets[bucket];
              const overflow = outlets.length - COLUMN_CHIPS;
              return (
                <ul
                  key={bucket}
                  aria-label={`${t(`filter.${bucket}`)} (${outlets.length})`}
                  title={outlets.length === 0 ? `${t(`filter.${bucket}`)} 0` : undefined}
                  className="mx-auto flex min-h-[3.25rem] w-10 flex-col items-center justify-center gap-1 rounded-full px-1 py-1.5"
                  style={
                    outlets.length > 0
                      ? { background: `hsl(var(--${LEAN_META[bucket].token}) / 0.12)` }
                      : {
                          backgroundImage:
                            "repeating-linear-gradient(45deg, transparent 0 4px, hsl(var(--muted-foreground) / 0.18) 4px 6px)",
                        }
                  }
                >
                  {outlets.slice(0, COLUMN_CHIPS).map((o) => (
                    <OutletChip key={o.publisher} outlet={o} />
                  ))}
                  {overflow > 0 && <OverflowChip label={`+${formatCompact(overflow)}`} />}
                </ul>
              );
            })}
          </div>
        </>
      )}

      {groups.untracked.length > 0 && (
        <div className="mt-3 border-t pt-3">
          <p className="text-[0.68rem] font-semibold uppercase tracking-wider text-muted-foreground">
            {t("story.untrackedBias")}
          </p>
          <ul className="mt-2 flex flex-wrap items-center gap-1.5">
            {groups.untracked.slice(0, UNTRACKED_CHIPS).map((o) => (
              <OutletChip key={o.publisher} outlet={o} />
            ))}
            {groups.untracked.length > UNTRACKED_CHIPS && (
              <OverflowChip label={`+${formatCompact(groups.untracked.length - UNTRACKED_CHIPS)}`} />
            )}
          </ul>
        </div>
      )}
    </div>
  );
}

function OutletChip({ outlet }: { outlet: OutletMark }) {
  const icons = hostIconCandidates(outlet.url);
  return (
    <li
      title={outlet.publisher}
      className="grid h-7 w-7 shrink-0 place-items-center overflow-hidden rounded-full border-2 border-card bg-muted"
    >
      <span className="sr-only">{outlet.publisher}</span>
      <PublisherLogo
        logo={icons[0]}
        fallbacks={icons.slice(1)}
        sizePx={24}
        className="h-6 w-6"
        fallbackNode={
          <span aria-hidden className="text-[0.55rem] font-bold text-muted-foreground">
            {monogram(outlet.publisher)}
          </span>
        }
      />
    </li>
  );
}

function OverflowChip({ label }: { label: string }) {
  return (
    <li className="grid h-7 w-7 shrink-0 place-items-center rounded-full border-2 border-dashed border-border bg-card text-[0.55rem] font-semibold text-muted-foreground">
      {label}
    </li>
  );
}
