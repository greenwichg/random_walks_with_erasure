"use client";

import * as React from "react";
import { motion } from "framer-motion";
import type { StoryCoverage } from "@ih/core/domain/types";
import type { OwnershipSlice } from "@ih/core/logic/ownership-distribution";
import {
  dominantOwnership,
  groupOutletsByOwnership,
} from "@ih/core/logic/ownership-distribution";
import { SectionHeader } from "@/components/shared/section-header";
import { InfoTooltip } from "@/components/shared/info-tooltip";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

type CatKey = OwnershipSlice["category"];

/** Fixed color + label assignment — a hue always means the same owner type (tokens are
 *  CVD-validated pairwise in ring order, both themes; `unknown` reuses the neutral center). */
const META: Record<CatKey, { token: string; labelKey: string }> = {
  independent: { token: "own-independent", labelKey: "own.independent" },
  individual: { token: "own-individual", labelKey: "own.individual" },
  telecom: { token: "own-telecom", labelKey: "own.telecom" },
  government: { token: "own-government", labelKey: "own.government" },
  private_equity: { token: "own-private-equity", labelKey: "own.privateEquity" },
  conglomerate: { token: "own-conglomerate", labelKey: "own.conglomerate" },
  corporation: { token: "own-corporation", labelKey: "own.corporation" },
  other: { token: "own-other", labelKey: "own.other" },
  unknown: { token: "center", labelKey: "own.unknown" },
};
const colorOf = (c: CatKey) => `hsl(var(--${META[c].token}))`;

// Radial geometry (viewBox 240x240): category ring inside, one tick per outlet outside,
// readout disc in the hole. Gaps are angular so they survive any radius.
const CX = 120;
const CY = 120;
const R_CAT = 76;
const W_CAT = 26;
const R_OUT = 101;
const W_OUT = 12;
const R_HOLE = 56;
const GAP_CAT = 2.5;
const GAP_OUT = 2.2;

const pt = (r: number, deg: number): [number, number] => {
  const a = ((deg - 90) * Math.PI) / 180;
  return [CX + r * Math.cos(a), CY + r * Math.sin(a)];
};
const arcPath = (r: number, a0: number, a1: number) => {
  const [x0, y0] = pt(r, a0);
  const [x1, y1] = pt(r, a1);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
};

/**
 * The OWNERSHIP panel (Ground News comparison, adapted to the house system): who controls the
 * outlets on this story. One summary sentence, a segmented share bar, a two-ring radial — the
 * inner ring is category shares, the outer ring one tick per outlet — and a legend with counts
 * and percentages. All four surfaces render from ONE `groupOutletsByOwnership` result and share
 * one hovered-category state, so hovering (or tapping) a legend row, a bar segment or an arc
 * highlights the same category everywhere and swaps the disc readout to it.
 *
 * Data honesty: ownership is the registry's sourced `ownership` column; outlets it doesn't
 * classify form the muted `unknown` slice, counted in every percentage (a story that is mostly
 * unclassified must say so) and never folded into "other". Nothing classified -> no panel:
 * a chart of pure unknown is not a visualization, it's an apology. Members only (M4).
 */
export function OwnershipPanel({ coverage }: { coverage: StoryCoverage[] }) {
  const { t, formatCompact } = useTranslation();
  const groups = React.useMemo(() => groupOutletsByOwnership(coverage), [coverage]);
  const [hover, setHover] = React.useState<CatKey | null>(null);
  const dominant = dominantOwnership(groups);
  if (groups.knownCount === 0) return null;

  const total = groups.totalOutlets;
  const pctOf = (s: OwnershipSlice) => Math.round((s.outlets.length / total) * 100);
  const label = (c: CatKey) => t(META[c].labelKey);
  const tip = (s: OwnershipSlice) => `${label(s.category)} · ${s.outlets.length} · ${pctOf(s)}%`;
  const dim = (c: CatKey) => hover !== null && hover !== c;

  // The readout the disc shows: the hovered slice, else the headline category. Both come from
  // rendered slices (hover is only ever set from them; dominant is one of them), so find hits.
  const readoutCat = hover ?? dominant!.category;
  const readoutSlice = groups.slices.find((s) => s.category === readoutCat)!;
  const readoutWords = label(readoutSlice.category).split(" ");

  // Category arcs and per-outlet ticks share one angular walk, so ticks sit exactly under
  // their category's arc. A lone slice becomes a full circle (an arc with equal endpoints
  // renders nothing).
  const slot = 360 / total;
  let angle = 0;
  const catArcs: { s: OwnershipSlice; a0: number; a1: number }[] = [];
  const ticks: { publisher: string; category: CatKey; a0: number; a1: number }[] = [];
  for (const s of groups.slices) {
    const span = s.outlets.length * slot;
    catArcs.push({ s, a0: angle, a1: angle + span });
    s.outlets.forEach((o, i) => {
      const t0 = angle + i * slot;
      ticks.push({ publisher: o.publisher, category: s.category, a0: t0, a1: t0 + slot });
    });
    angle += span;
  }

  const interactive = (c: CatKey) => ({
    onMouseEnter: () => setHover(c),
    onMouseLeave: () => setHover(null),
    onClick: () => setHover((h) => (h === c ? null : c)),
  });

  return (
    <section aria-labelledby="story-ownership-heading" className="rounded-lg border bg-card p-4">
      <div className="mb-3 flex items-center gap-1.5">
        <SectionHeader id="story-ownership-heading" title={t("story.ownership")} className="mb-0" />
        <InfoTooltip text={t("story.ownershipInfo")} />
      </div>

      <p className="mb-2.5 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <span
          aria-hidden
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ background: colorOf(dominant!.category) }}
        />
        {t("story.ownershipSummary", { pct: dominant!.pct, category: label(dominant!.category) })}
      </p>

      {/* The share bar — same segments, same colors, same hover as the ring and the legend. */}
      <div className="flex h-2.5 overflow-hidden rounded-full bg-muted" aria-hidden>
        {groups.slices.map((s, i) => (
          <motion.div
            key={s.category}
            initial={{ width: 0 }}
            animate={{ width: `${(s.outlets.length / total) * 100}%` }}
            transition={{ delay: i * 0.06, duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            title={tip(s)}
            className="transition-opacity"
            style={{ background: colorOf(s.category), opacity: dim(s.category) ? 0.35 : 1 }}
            {...interactive(s.category)}
          />
        ))}
      </div>

      <svg
        viewBox="0 0 240 240"
        role="img"
        aria-label={groups.slices.map(tip).join(", ")}
        className="mx-auto mt-3 block w-full max-w-[240px]"
        onMouseLeave={() => setHover(null)}
      >
        {catArcs.map(({ s, a0, a1 }) =>
          a1 - a0 >= 359.9 ? (
            <circle
              key={s.category}
              cx={CX}
              cy={CY}
              r={R_CAT}
              fill="none"
              stroke={colorOf(s.category)}
              strokeWidth={W_CAT}
            >
              <title>{tip(s)}</title>
            </circle>
          ) : (
            <path
              key={s.category}
              d={arcPath(R_CAT, a0 + GAP_CAT / 2, a1 - GAP_CAT / 2)}
              fill="none"
              stroke={colorOf(s.category)}
              strokeWidth={W_CAT}
              className="cursor-pointer transition-opacity"
              opacity={dim(s.category) ? 0.35 : 1}
              {...interactive(s.category)}
            >
              <title>{tip(s)}</title>
            </path>
          ),
        )}
        {total > 1 &&
          ticks.map(({ publisher, category, a0, a1 }) => (
            <path
              key={publisher}
              d={arcPath(R_OUT, a0 + GAP_OUT / 2, a1 - GAP_OUT / 2)}
              fill="none"
              stroke={colorOf(category)}
              strokeWidth={W_OUT}
              className="cursor-pointer transition-opacity"
              opacity={dim(category) ? 0.35 : 1}
              {...interactive(category)}
            >
              <title>{`${publisher} · ${label(category)}`}</title>
            </path>
          ))}
        <circle cx={CX} cy={CY} r={R_HOLE} fill="hsl(var(--muted))" />
        <text
          x={CX}
          y={CY - 6}
          textAnchor="middle"
          className="fill-foreground font-bold"
          style={{ fontSize: 22, fontVariantNumeric: "tabular-nums" }}
        >
          {pctOf(readoutSlice)}%
        </text>
        {readoutWords.slice(0, 2).map((w, i) => (
          <text
            key={i}
            x={CX}
            y={CY + 10 + i * 11}
            textAnchor="middle"
            className="fill-muted-foreground font-medium"
            style={{ fontSize: 9 }}
          >
            {w}
          </text>
        ))}
      </svg>

      <ul className="mt-3 space-y-0.5">
        {groups.slices.map((s) => (
          <li key={s.category}>
            <button
              type="button"
              aria-pressed={hover === s.category}
              className={cn(
                "flex w-full items-center gap-2 rounded px-1.5 py-1 text-xs transition-opacity",
                "hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                dim(s.category) && "opacity-40",
              )}
              onFocus={() => setHover(s.category)}
              onBlur={() => setHover(null)}
              {...interactive(s.category)}
            >
              <span
                aria-hidden
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ background: colorOf(s.category) }}
              />
              <span
                className={cn(
                  "min-w-0 flex-1 truncate text-left",
                  s.category === "unknown" ? "text-muted-foreground" : "text-foreground",
                )}
              >
                {label(s.category)}
              </span>
              <span className="text-muted-foreground">{formatCompact(s.outlets.length)}</span>
              <span className="w-9 text-right font-medium tabular-nums">{pctOf(s)}%</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
