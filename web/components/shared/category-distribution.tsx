"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * THE distribution chart: a segmented share bar, a two-ring radial (categories inside, one tick
 * per outlet outside, a readout disc in the hole) and a legend with counts and percentages — all
 * three driven by ONE slice list and ONE hovered-key state, so hovering a legend row, a bar
 * segment or an arc highlights the same category everywhere and swaps the disc's readout to it.
 *
 * Extracted from the Ownership panel when Factuality needed the identical picture over a
 * different vocabulary. It is deliberately generic about MEANING and strict about SHAPE: it knows
 * nothing about owners or raters, only that a slice has a colour, a label and a list of outlets —
 * which is what keeps the two panels from drifting into two chart implementations that answer the
 * same question differently.
 *
 * Percentages are over ALL outlets, unknown/unrated included, so the numbers in the legend always
 * agree with the widths in the bar and the arcs in the ring.
 */

export interface DistributionSlice {
  /** Stable identity for hover + React keys — the caller's category token. */
  key: string;
  label: string;
  /** A resolved CSS colour (the caller's meta table owns the token → colour mapping). */
  color: string;
  /** One entry per OUTLET: the outer ring draws a tick for each and names it on hover. */
  outlets: { publisher: string }[];
  /** The "not a category" slice (unknown owner, unrated outlet) — muted in the legend. */
  muted?: boolean;
}

// Radial geometry (viewBox 240x240). Gaps are angular so they survive any radius.
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

export function CategoryDistribution({
  slices,
  /** Which slice the readout disc shows when nothing is hovered — the panel's headline category. */
  defaultKey,
  className,
}: {
  slices: DistributionSlice[];
  defaultKey: string;
  className?: string;
}) {
  const { formatCompact } = useTranslation();
  const [hover, setHover] = React.useState<string | null>(null);

  const total = slices.reduce((n, s) => n + s.outlets.length, 0);
  if (total === 0) return null;

  const pctOf = (s: DistributionSlice) => Math.round((s.outlets.length / total) * 100);
  const tip = (s: DistributionSlice) => `${s.label} · ${s.outlets.length} · ${pctOf(s)}%`;
  const dim = (key: string) => hover !== null && hover !== key;

  // The readout: the hovered slice, else the caller's headline slice, else the first rendered one
  // (a caller whose headline slice is absent still gets a disc rather than a crash).
  const readout =
    slices.find((s) => s.key === hover) ?? slices.find((s) => s.key === defaultKey) ?? slices[0];
  if (!readout) return null;
  const readoutWords = readout.label.split(" ");
  const labelOf = new Map(slices.map((s) => [s.key, s.label]));

  // Category arcs and per-outlet ticks share one angular walk, so ticks sit exactly under their
  // category's arc. A lone slice becomes a full circle (an arc with equal endpoints draws nothing).
  const slot = 360 / total;
  let angle = 0;
  const catArcs: { s: DistributionSlice; a0: number; a1: number }[] = [];
  const ticks: { publisher: string; key: string; color: string; a0: number; a1: number }[] = [];
  for (const s of slices) {
    const span = s.outlets.length * slot;
    catArcs.push({ s, a0: angle, a1: angle + span });
    s.outlets.forEach((o, i) => {
      const t0 = angle + i * slot;
      ticks.push({ publisher: o.publisher, key: s.key, color: s.color, a0: t0, a1: t0 + slot });
    });
    angle += span;
  }

  const interactive = (key: string) => ({
    onMouseEnter: () => setHover(key),
    onMouseLeave: () => setHover(null),
    onClick: () => setHover((h) => (h === key ? null : key)),
  });

  return (
    <div className={className}>
      <div className="flex h-2.5 overflow-hidden rounded-full bg-muted" aria-hidden>
        {slices.map((s, i) => (
          <motion.div
            key={s.key}
            initial={{ width: 0 }}
            animate={{ width: `${(s.outlets.length / total) * 100}%` }}
            transition={{ delay: i * 0.06, duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            title={tip(s)}
            className="transition-opacity"
            style={{ background: s.color, opacity: dim(s.key) ? 0.35 : 1 }}
            {...interactive(s.key)}
          />
        ))}
      </div>

      <svg
        viewBox="0 0 240 240"
        role="img"
        aria-label={slices.map(tip).join(", ")}
        className="mx-auto mt-3 block w-full max-w-[240px]"
        onMouseLeave={() => setHover(null)}
      >
        {catArcs.map(({ s, a0, a1 }) =>
          a1 - a0 >= 359.9 ? (
            <circle key={s.key} cx={CX} cy={CY} r={R_CAT} fill="none" stroke={s.color} strokeWidth={W_CAT}>
              <title>{tip(s)}</title>
            </circle>
          ) : (
            <path
              key={s.key}
              d={arcPath(R_CAT, a0 + GAP_CAT / 2, a1 - GAP_CAT / 2)}
              fill="none"
              stroke={s.color}
              strokeWidth={W_CAT}
              className="cursor-pointer transition-opacity"
              opacity={dim(s.key) ? 0.35 : 1}
              {...interactive(s.key)}
            >
              <title>{tip(s)}</title>
            </path>
          ),
        )}
        {total > 1 &&
          ticks.map(({ publisher, key, color, a0, a1 }) => (
            <path
              key={publisher}
              d={arcPath(R_OUT, a0 + GAP_OUT / 2, a1 - GAP_OUT / 2)}
              fill="none"
              stroke={color}
              strokeWidth={W_OUT}
              className="cursor-pointer transition-opacity"
              opacity={dim(key) ? 0.35 : 1}
              {...interactive(key)}
            >
              <title>{`${publisher} · ${labelOf.get(key) ?? ""}`}</title>
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
          {pctOf(readout)}%
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
        {slices.map((s) => (
          <li key={s.key}>
            <button
              type="button"
              aria-pressed={hover === s.key}
              className={cn(
                "flex w-full items-center gap-2 rounded px-1.5 py-1 text-xs transition-opacity",
                "hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                dim(s.key) && "opacity-40",
              )}
              onFocus={() => setHover(s.key)}
              onBlur={() => setHover(null)}
              {...interactive(s.key)}
            >
              <span
                aria-hidden
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{ background: s.color }}
              />
              <span
                className={cn(
                  "min-w-0 flex-1 truncate text-left",
                  s.muted ? "text-muted-foreground" : "text-foreground",
                )}
              >
                {s.label}
              </span>
              <span className="text-muted-foreground">{formatCompact(s.outlets.length)}</span>
              <span className="w-9 text-right font-medium tabular-nums">{pctOf(s)}%</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
