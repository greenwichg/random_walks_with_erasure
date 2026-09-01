"use client";

import * as React from "react";
import Link from "next/link";
import type { LeanBucket } from "@ih/core/domain/types";
import type { BiasGroups, OutletMark } from "@ih/core/logic/bias-distribution";
import { BIAS_BUCKETS, dominantBucket, splitAtCap } from "@ih/core/logic/bias-distribution";
import { hostIconCandidates, logoCandidates } from "@ih/core/logic/publisher-logo";
import { monogram } from "@ih/core/logic/placeholder-art";
import { LEAN_META } from "@ih/core/logic/metrics";
import { PublisherLogo } from "@/components/shared/publisher-logo";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { useTranslation } from "@/lib/i18n";

const COLUMN_CHIPS = 5;
const UNTRACKED_CHIPS = 8;

/** Which capsule's overflow is open. `untracked` is a group here but never a lean bucket. */
type PanelKey = LeanBucket | "untracked";

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
 *
 * A capsule that overflows ends in a `+N` chip, and that chip OPENS the N it names: the hidden
 * outlets list in a sheet — a bottom drawer on a phone, a compact centered panel from `sm` up.
 * Chip and list are cut by one `splitAtCap` call, so the promised number and the delivered list
 * cannot drift. Rows link to the outlet's profile, like every other publisher mention.
 */
export function BiasDistribution({ groups }: { groups: BiasGroups }) {
  const { t, formatCompact } = useTranslation();
  const [panel, setPanel] = React.useState<PanelKey | null>(null);
  const dominant = dominantBucket(groups);
  if (groups.ratedCount === 0 && groups.untracked.length === 0) return null;

  const labelOf = (key: PanelKey) =>
    key === "untracked" ? t("story.untrackedBias") : t(`filter.${key}`);
  const colorOf = (key: PanelKey) =>
    key === "untracked" ? "hsl(var(--muted-foreground))" : LEAN_META[key].color;
  const hiddenOf = (key: PanelKey) =>
    key === "untracked"
      ? splitAtCap(groups.untracked, UNTRACKED_CHIPS).hidden
      : splitAtCap(groups.buckets[key], COLUMN_CHIPS).hidden;

  const openOutlets = panel ? hiddenOf(panel) : [];

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
              const { shown, hidden } = splitAtCap(outlets, COLUMN_CHIPS);
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
                  {shown.map((o) => (
                    <OutletChip key={o.publisher} outlet={o} />
                  ))}
                  {hidden.length > 0 && (
                    <OverflowChip
                      label={`+${formatCompact(hidden.length)}`}
                      title={`${t(`filter.${bucket}`)} — ${t("story.moreOutlets", { n: hidden.length })}`}
                      onClick={() => setPanel(bucket)}
                    />
                  )}
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
            {splitAtCap(groups.untracked, UNTRACKED_CHIPS).shown.map((o) => (
              <OutletChip key={o.publisher} outlet={o} />
            ))}
            {groups.untracked.length > UNTRACKED_CHIPS && (
              <OverflowChip
                label={`+${formatCompact(groups.untracked.length - UNTRACKED_CHIPS)}`}
                title={`${t("story.untrackedBias")} — ${t("story.moreOutlets", {
                  n: groups.untracked.length - UNTRACKED_CHIPS,
                })}`}
                onClick={() => setPanel("untracked")}
              />
            )}
          </ul>
        </div>
      )}

      {/* One sheet for every capsule — the open key decides its contents, so there is a single
          dismissal path (overlay press, Escape, the close button) rather than four. Radix owns
          the focus trap and the scroll lock. */}
      <Sheet open={panel !== null} onOpenChange={(next) => !next && setPanel(null)}>
        <SheetContent
          side="bottom"
          className="flex flex-col overflow-hidden rounded-t-2xl sm:inset-0 sm:m-auto sm:h-fit sm:max-h-[70vh] sm:w-[21rem] sm:max-w-[calc(100vw-2rem)] sm:rounded-2xl sm:border"
        >
          <div className="shrink-0 px-4 pb-1 pt-4">
            <SheetTitle className="flex items-center gap-2 text-sm">
              <span
                aria-hidden
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ background: panel ? colorOf(panel) : undefined }}
              />
              {panel ? labelOf(panel) : ""}
            </SheetTitle>
            <SheetDescription className="mt-0.5 text-xs">
              {t("story.moreOutlets", { n: openOutlets.length })}
            </SheetDescription>
          </div>
          <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-4 pt-1">
            {openOutlets.map((o) => (
              <li key={o.publisher}>
                <Link
                  href={`/publishers/${encodeURIComponent(o.publisher)}`}
                  className="flex items-center gap-2.5 rounded-md px-2 py-2 transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="grid h-7 w-7 shrink-0 place-items-center overflow-hidden rounded-full bg-muted">
                    <OutletIcon outlet={o} />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-sm font-medium">{o.publisher}</span>
                  <span
                    className="shrink-0 text-[0.68rem] font-medium"
                    style={{ color: panel ? colorOf(panel) : undefined }}
                  >
                    {panel === "untracked" ? t("lean.unknown") : panel ? labelOf(panel) : ""}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </SheetContent>
      </Sheet>
    </div>
  );
}

/** The outlet's mark at chip size — the server-resolved logo first when the row carried one,
 *  then the shared host-derived walk, monogram terminal. */
function OutletIcon({ outlet, sizePx = 24 }: { outlet: OutletMark; sizePx?: number }) {
  const icons = logoCandidates(outlet.logo, outlet.logoFallbacks ?? hostIconCandidates(outlet.url));
  return (
    <PublisherLogo
      logo={icons[0]}
      fallbacks={icons.slice(1)}
      sizePx={sizePx}
      className="h-6 w-6"
      fallbackNode={
        <span aria-hidden className="text-[0.55rem] font-bold text-muted-foreground">
          {monogram(outlet.publisher)}
        </span>
      }
    />
  );
}

function OutletChip({ outlet }: { outlet: OutletMark }) {
  return (
    <li
      title={outlet.publisher}
      className="grid h-7 w-7 shrink-0 place-items-center overflow-hidden rounded-full border-2 border-card bg-muted"
    >
      <span className="sr-only">{outlet.publisher}</span>
      <OutletIcon outlet={outlet} />
    </li>
  );
}

/** The `+N` chip — same mark as before, now the control that opens the N it names. */
function OverflowChip({
  label,
  title,
  onClick,
}: {
  label: string;
  title: string;
  onClick: () => void;
}) {
  return (
    <li className="shrink-0">
      <button
        type="button"
        title={title}
        aria-label={title}
        onClick={onClick}
        className="grid h-7 w-7 place-items-center rounded-full border-2 border-dashed border-border bg-card text-[0.55rem] font-semibold text-muted-foreground transition-colors hover:border-solid hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {label}
      </button>
    </li>
  );
}
