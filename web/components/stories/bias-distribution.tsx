"use client";

import * as React from "react";
import Link from "next/link";
import type { LeanBucket } from "@ih/core/domain/types";
import type { BiasGroups, OutletMark } from "@ih/core/logic/bias-distribution";
import { BIAS_BUCKETS, dominantBucket, splitAtCap } from "@ih/core/logic/bias-distribution";
import { LEAN_META } from "@ih/core/logic/metrics";
import { OutletAvatar } from "@/components/shared/outlet-avatar";
import { useReadArticleAction } from "@/components/shared/read-article-button";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Where a chip's read is recorded from — its own tag, so opens from the bias card can be told
 *  apart from the coverage list's Read button in the same pipeline. */
const OPENED_FROM = "story-bias";

/**
 * Mark sizes, in CSS px. These are legibility decisions, and because `PublisherLogo` treats the
 * content box as a RESOLUTION DEMAND (see outlet-avatar.tsx), they are also quality decisions: the
 * old 24px chip accepted a 32px favicon and drew it soft, where a 48px plate asks for a real
 * ~64px asset and walks past anything that cannot supply one.
 *
 * The untracked strip runs a size smaller on purpose. Those outlets are shown but NOT counted, and
 * a strip drawn at the capsules' weight would read as a fourth column of the distribution.
 */
const CHIP_PX = 48;
const UNTRACKED_PX = 40;
const ROW_PX = 32;

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
                  // w-14 = the 48px plate plus its 4px gutter each side; gap-2 is the air the
                  // old gap-1 never gave the marks, which is most of why they read as compressed.
                  // Capsules are equal height (the grid stretches them) and their marks stack from
                  // the TOP, so how far down a stack reaches is itself the count — the three sides
                  // stay comparable at a glance. Centring the marks threw that away and left a
                  // short side floating in the middle of its own capsule.
                  className="mx-auto flex min-h-[4rem] w-14 flex-col items-center justify-start gap-2 rounded-full px-1 py-2"
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
                    <OutletChip key={o.publisher} outlet={o} size={CHIP_PX} />
                  ))}
                  {hidden.length > 0 && (
                    <OverflowChip
                      label={`+${formatCompact(hidden.length)}`}
                      title={`${t(`filter.${bucket}`)} — ${t("story.moreOutlets", { n: hidden.length })}`}
                      onClick={() => setPanel(bucket)}
                      size={CHIP_PX}
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
          <ul className="mt-2 flex flex-wrap items-center gap-2">
            {splitAtCap(groups.untracked, UNTRACKED_CHIPS).shown.map((o) => (
              <OutletChip key={o.publisher} outlet={o} size={UNTRACKED_PX} />
            ))}
            {groups.untracked.length > UNTRACKED_CHIPS && (
              <OverflowChip
                label={`+${formatCompact(groups.untracked.length - UNTRACKED_CHIPS)}`}
                title={`${t("story.untrackedBias")} — ${t("story.moreOutlets", {
                  n: groups.untracked.length - UNTRACKED_CHIPS,
                })}`}
                onClick={() => setPanel("untracked")}
                size={UNTRACKED_PX}
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
              <HiddenOutletRow
                key={o.publisher}
                outlet={o}
                label={panel === "untracked" ? t("lean.unknown") : panel ? labelOf(panel) : ""}
                color={panel ? colorOf(panel) : undefined}
              />
            ))}
          </ul>
        </SheetContent>
      </Sheet>
    </div>
  );
}

/**
 * One outlet's chip: a button that opens the outlet's NEWEST article on this story through the
 * shared Read pipeline — recorded like every other read, tagged `story-bias` — with the headline
 * in the tooltip so the promise is visible before the click. The mark itself is unchanged; the
 * affordance is the cursor, a ring, and a slight lift on hover/focus. An outlet whose rows carried
 * no URL stays a plain mark: no affordance is offered that cannot be kept.
 */
function OutletChip({ outlet, size }: { outlet: OutletMark; size: number }) {
  const { t } = useTranslation();
  const { actionable, opened, open } = useReadArticleAction(
    { url: outlet.url, headline: outlet.headline },
    OPENED_FROM,
  );
  if (!actionable) {
    return (
      <li title={outlet.publisher} className="shrink-0">
        <span className="sr-only">{outlet.publisher}</span>
        <OutletAvatar outlet={outlet} size={size} />
      </li>
    );
  }
  const label = t("story.openArticleFrom", { publisher: outlet.publisher, headline: outlet.headline ?? "" });
  return (
    <li className="shrink-0">
      <button
        type="button"
        onClick={open}
        title={label}
        aria-label={label}
        aria-pressed={opened}
        className={cn(
          "block rounded-full transition-transform hover:scale-105",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
        )}
      >
        <OutletAvatar
          outlet={outlet}
          size={size}
          // The hover/opened state rides the plate's own ring, so the mark keeps its full box
          // instead of losing pixels to a border drawn inside it.
          className={cn(
            "transition-shadow hover:ring-2 hover:ring-ring",
            opened && "ring-2 ring-positive/60",
          )}
        />
      </button>
    </li>
  );
}

/**
 * A hidden outlet in the +N sheet: the same article open as the chip, with the headline shown
 * as a second line — for a capsule's overflow this IS the article list. An outlet with no URL
 * falls back to its profile page, so the row is never a dead end.
 */
function HiddenOutletRow({ outlet, label, color }: { outlet: OutletMark; label: string; color?: string }) {
  const { t } = useTranslation();
  const { actionable, opened, open } = useReadArticleAction(
    { url: outlet.url, headline: outlet.headline },
    OPENED_FROM,
  );
  const body = (
    <>
      <OutletAvatar outlet={outlet} size={ROW_PX} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{outlet.publisher}</span>
        {outlet.headline && (
          <span className="block truncate text-xs text-muted-foreground">{outlet.headline}</span>
        )}
      </span>
      <span className="shrink-0 text-[0.68rem] font-medium" style={{ color }}>
        {label}
      </span>
    </>
  );
  const rowClass =
    "flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  return (
    <li>
      {actionable ? (
        <button
          type="button"
          onClick={open}
          aria-pressed={opened}
          title={t("story.openArticleFrom", { publisher: outlet.publisher, headline: outlet.headline ?? "" })}
          className={rowClass}
        >
          {body}
        </button>
      ) : (
        <Link href={`/publishers/${encodeURIComponent(outlet.publisher)}`} className={rowClass}>
          {body}
        </Link>
      )}
    </li>
  );
}

/** The `+N` chip — same mark as before, now the control that opens the N it names. */
function OverflowChip({
  label,
  title,
  onClick,
  size,
}: {
  label: string;
  title: string;
  onClick: () => void;
  size: number;
}) {
  return (
    <li className="shrink-0">
      <button
        type="button"
        title={title}
        aria-label={title}
        onClick={onClick}
        // Deliberately NOT on a white plate: this is the house's own control, not a publisher's
        // mark, and the dashed themed circle is what keeps "+6" from reading as a seventh outlet.
        className="grid place-items-center rounded-full border border-dashed border-border bg-card font-semibold text-muted-foreground transition-colors hover:border-solid hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        style={{ width: size, height: size, fontSize: Math.round(size * 0.26) }}
      >
        {label}
      </button>
    </li>
  );
}
