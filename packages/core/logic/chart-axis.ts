/**
 * One place that decides every analytics axis: its range, its ticks, and its labels.
 *
 * Two failures this exists to prevent, both of which shipped:
 *
 * 1. **Auto-scaled score axes.** `TrendChart` defaulted to `["dataMin - 6", "dataMax + 6"]`, so a
 *    reader whose Information Health sat between 68 and 72 saw a chart that filled its whole
 *    height — a 4-point wobble drawn as a mountain range. A 0–100 score belongs on a 0–100 axis,
 *    always, so the four score cards can be compared against each other and against yesterday.
 * 2. **Repeated date labels.** The score series is one point per SAVED REPORT, and
 *    `store.report_metric_series` stamps each with its UTC *day* — so a reader who generated
 *    twelve reports on one afternoon got twelve points all labelled "Aug 19", an axis that looks
 *    like twelve days of history and is one. Labels de-duplicate here, and each run of identical
 *    labels is marked once at its midpoint, the way a band axis is labelled.
 *
 * Pure and framework-free: no recharts import, no React. That is what makes it testable, and the
 * tests are the guard — see chart-axis.test.ts.
 */

/** What a series measures. The axis follows from this, never from the data's own extent. */
export type AxisKind = "score" | "percent" | "count";

/** Information Health and its metrics are normalized 0–100 indices. */
export const SCORE_DOMAIN: readonly [number, number] = [0, 100];
export const SCORE_TICKS: readonly number[] = [0, 25, 50, 75, 100];

/** Shares arrive as fractions (0.42), and are shown as percentages (42%). */
export const PERCENT_DOMAIN: readonly [number, number] = [0, 1];
export const PERCENT_TICKS: readonly number[] = [0, 0.25, 0.5, 0.75, 1];

/**
 * A round step at or above `rough`, from the 1–2–5 ladder: 0.7→1, 3→5, 42→50, 380→500.
 * Round steps are what make a count axis readable; recharts' own choice is derived from the data
 * max and lands on values like 1450.
 */
export function niceStep(rough: number): number {
  if (!(rough > 0) || !Number.isFinite(rough)) return 1;
  const mag = 10 ** Math.floor(Math.log10(rough));
  const norm = rough / mag;
  return (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
}

/**
 * A count axis: zero-anchored (a bar chart that does not start at zero misstates every ratio it
 * draws), topped at a round multiple of a round step, and integer-stepped because half an article
 * does not exist. An empty or all-zero series gets [0, 1] rather than [0, 0] — a degenerate axis
 * renders as a single line and reads as a broken chart rather than an empty one.
 */
export function countAxis(max: number, targetTicks = 4): { domain: [number, number]; ticks: number[] } {
  const top = Number.isFinite(max) ? Math.max(0, max) : 0;
  if (top <= 0) return { domain: [0, 1], ticks: [0, 1] };
  const step = Math.max(1, Math.round(niceStep(top / Math.max(1, targetTicks))));
  const ceiling = Math.ceil(top / step) * step;
  const ticks: number[] = [];
  for (let v = 0; v <= ceiling; v += step) ticks.push(v);
  return { domain: [0, ceiling], ticks };
}

/** 0.25 → "25%". Rounded to whole percent: the underlying shares are noisier than a decimal place. */
export function formatPercent(v: number): string {
  return `${Math.round((Number(v) || 0) * 100)}%`;
}

/**
 * A score tick. Deliberately BARE — no "%" suffix. Information Health 72 is an index point on a
 * 0–100 scale, not 72% of anything, and a percent sign here would be a label that lies.
 */
export function formatScore(v: number): string {
  return `${Math.round(Number(v) || 0)}`;
}

/** A count tick. Compact only past 10,000, where full digits crowd the axis gutter. */
export function formatCount(v: number): string {
  const n = Number(v) || 0;
  if (Math.abs(n) >= 10000) {
    const k = n / 1000;
    return `${Number.isInteger(k) ? k : k.toFixed(1)}k`;
  }
  return `${n}`;
}

/**
 * Tooltip text. A tick may abbreviate to fit the gutter; a tooltip may not — it is the one place
 * a reader goes for the exact value, so "12.5k" there answers a question with a rounding.
 */
export function exactFormat(kind: AxisKind): (v: number) => string {
  return kind === "percent" ? formatPercent : (v: number) => `${Number(v) || 0}`;
}

/** The complete y-axis for a series: range, ticks and tick text, chosen by what it measures. */
export function yAxis(
  kind: AxisKind,
  max = 0,
): { domain: [number, number]; ticks: number[]; format: (v: number) => string } {
  if (kind === "percent") {
    return { domain: [...PERCENT_DOMAIN], ticks: [...PERCENT_TICKS], format: formatPercent };
  }
  if (kind === "score") {
    return { domain: [...SCORE_DOMAIN], ticks: [...SCORE_TICKS], format: formatScore };
  }
  return { ...countAxis(max), format: formatCount };
}

/**
 * The largest value a chart must fit — the only input a count axis takes from the data.
 *
 * `stacked` decides what "largest" means: stacked bars are as tall as their row's SUM, so taking
 * the per-key max would top the axis below the tallest bar and clip it.
 */
export function seriesMax(
  rows: readonly Record<string, unknown>[],
  keys: readonly string[],
  stacked = false,
): number {
  let max = 0;
  for (const row of rows) {
    let rowMax = 0;
    for (const k of keys) {
      const v = Number(row?.[k]);
      if (!Number.isFinite(v)) continue;
      if (stacked) rowMax += Math.max(0, v);
      else if (v > rowMax) rowMax = v;
    }
    if (rowMax > max) max = rowMax;
  }
  return max;
}

/** A maximal run of consecutive points sharing one formatted label. */
export interface LabelRun {
  label: string;
  start: number;
  end: number;
  /** Where the label is drawn: the run's midpoint, so a 12-point day is marked under its middle. */
  at: number;
}

/** Group consecutive equal labels. `["a","a","b"]` → one run for "a" (0–1) and one for "b" (2–2). */
export function labelRuns(labels: readonly string[]): LabelRun[] {
  const runs: LabelRun[] = [];
  labels.forEach((label, i) => {
    const last = runs[runs.length - 1];
    if (last && last.label === label) {
      last.end = i;
      last.at = Math.floor((last.start + last.end) / 2);
    } else {
      runs.push({ label, start: i, end: i, at: i });
    }
  });
  return runs;
}

/**
 * Per-index tick text for a categorical x-axis: one label per RUN of equal labels, placed at the
 * run's midpoint, thinned to `capacity` labels so they cannot collide. Every other index gets ""
 * — the slot stays (recharts keeps tick index aligned to row index) but prints nothing.
 *
 * `capacity` is how many labels the axis has room for; derive it from the measured pixel width.
 */
export function tickLabels(
  values: readonly unknown[],
  format: (v: unknown) => string,
  capacity: number,
): string[] {
  const out = values.map(() => "");
  const runs = labelRuns(values.map(format));
  const room = Math.max(1, Math.floor(capacity));
  // Placed greedily with a minimum gap rather than by taking every n-th run. Counting runs alone
  // bounds how MANY labels appear but not how far apart they sit, so three single-point days at
  // the head of a long same-day run would all be drawn on top of each other while the budget said
  // there was plenty of room. The gap is in index units — `capacity` labels spread over
  // `values.length` points.
  const minGap = values.length / room;
  let last = -Infinity;
  for (const run of runs) {
    if (run.at - last < minGap) continue;
    out[run.at] = run.label;
    last = run.at;
  }
  return out;
}

/** Roughly the width of a "MMM D" label at 11px; below this two labels touch. */
export const TICK_LABEL_PX = 48;

/** How many x labels fit in `width` px. Zero width (pre-measure) yields 1, never 0 or NaN. */
export function tickCapacity(width: number): number {
  if (!Number.isFinite(width) || width <= 0) return 1;
  return Math.max(1, Math.floor(width / TICK_LABEL_PX));
}
