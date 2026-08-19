import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  PERCENT_TICKS,
  SCORE_TICKS,
  countAxis,
  formatCount,
  formatPercent,
  formatScore,
  labelRuns,
  niceStep,
  seriesMax,
  tickCapacity,
  tickLabels,
  yAxis,
} from "./chart-axis.ts";

/* ── ranges ─────────────────────────────────────────────────────────────────────────────────── */

test("a score axis is always 0–100, whatever the data does", () => {
  // The bug: `["dataMin - 6", "dataMax + 6"]` drew a reader sitting between 68 and 72 as a
  // full-height mountain range, and made two cards side by side incomparable.
  const axis = yAxis("score");
  assert.deepEqual(axis.domain, [0, 100]);
  assert.deepEqual(axis.ticks, [...SCORE_TICKS]);
});

test("a percent axis is always 0–100%, never the data's own extent", () => {
  const axis = yAxis("percent");
  assert.deepEqual(axis.domain, [0, 1]);
  assert.deepEqual(axis.ticks, [...PERCENT_TICKS]);
});

test("every count axis starts at zero", () => {
  // A bar drawn against a non-zero baseline misstates every ratio a reader takes off the chart.
  for (const max of [1, 7, 38, 250, 1450, 99999]) {
    assert.equal(countAxis(max).domain[0], 0, `max=${max} must be zero-anchored`);
    assert.equal(countAxis(max).ticks[0], 0);
  }
});

test("a count axis reaches its data and never clips it", () => {
  for (const max of [1, 3, 7, 38, 250, 1450, 4321]) {
    const { domain, ticks } = countAxis(max);
    assert.ok(domain[1] >= max, `${max} must fit inside ${domain[1]}`);
    assert.equal(ticks[ticks.length - 1], domain[1], "the top tick is the top of the axis");
  }
});

test("count ticks are round and evenly spaced", () => {
  assert.deepEqual(countAxis(38).ticks, [0, 10, 20, 30, 40]);
  assert.deepEqual(countAxis(1450).ticks, [0, 500, 1000, 1500]);
  assert.deepEqual(countAxis(3).ticks, [0, 1, 2, 3]);
});

test("count ticks are whole numbers — half an article does not exist", () => {
  for (const max of [1, 2, 3, 5, 9]) {
    for (const t of countAxis(max).ticks) {
      assert.ok(Number.isInteger(t), `tick ${t} for max=${max} must be a whole count`);
    }
  }
});

test("an empty or all-zero series gets a real axis, not a degenerate one", () => {
  // [0,0] collapses the plot to a single line, which reads as a broken chart rather than no data.
  for (const max of [0, -5, NaN, Infinity]) {
    const { domain, ticks } = countAxis(max as number);
    assert.deepEqual(domain, [0, 1]);
    assert.deepEqual(ticks, [0, 1]);
  }
});

test("niceStep climbs the 1–2–5 ladder", () => {
  assert.equal(niceStep(0.7), 1);
  assert.equal(niceStep(1), 1);
  assert.equal(niceStep(1.5), 2);
  assert.equal(niceStep(3), 5);
  assert.equal(niceStep(9.5), 10);
  assert.equal(niceStep(362.5), 500);
  assert.equal(niceStep(0), 1, "a degenerate input must not produce a zero or negative step");
});

/* ── the series max that feeds a count axis ─────────────────────────────────────────────────── */

test("stacked bars are measured by their row total, unstacked by their tallest bar", () => {
  const rows = [
    { date: "2026-08-17", accepted: 30, ignored: 40 },
    { date: "2026-08-18", accepted: 50, ignored: 10 },
  ];
  assert.equal(seriesMax(rows, ["accepted", "ignored"], false), 50, "tallest single bar");
  assert.equal(seriesMax(rows, ["accepted", "ignored"], true), 70, "tallest stack (30+40)");
  // Using the unstacked max for a stacked chart tops the axis at 50 and clips a 70-tall bar.
  assert.ok(countAxis(seriesMax(rows, ["accepted", "ignored"], true)).domain[1] >= 70);
});

test("seriesMax ignores non-numeric cells rather than returning NaN", () => {
  const rows = [{ date: "2026-08-17", overall: 12 }, { date: "2026-08-18", overall: null }];
  assert.equal(seriesMax(rows as never, ["overall"]), 12);
  assert.equal(seriesMax([], ["overall"]), 0);
});

/* ── tick text ──────────────────────────────────────────────────────────────────────────────── */

test("a percent tick carries its unit; a score tick does not", () => {
  assert.equal(formatPercent(0.25), "25%");
  assert.equal(formatPercent(1), "100%");
  // Information Health 72 is an index point on a 0–100 scale, not 72% of anything. A "%" here
  // would be a label that lies about what the metric is.
  assert.equal(formatScore(72), "72");
  assert.equal(formatScore(72.4), "72");
});

test("counts stay literal until they would crowd the gutter", () => {
  assert.equal(formatCount(0), "0");
  assert.equal(formatCount(1500), "1500");
  assert.equal(formatCount(10000), "10k");
  assert.equal(formatCount(12500), "12.5k");
});

/* ── x-axis labels ──────────────────────────────────────────────────────────────────────────── */

const day = (d: unknown) => String(d ?? "").slice(5); // "2026-08-19" -> "08-19"

test("twelve reports on one day produce ONE label, not twelve", () => {
  // The exact defect from the screenshot: the score series is one point per saved report, dated by
  // UTC day, so an afternoon of reports repeated "Aug 19" across the whole axis and made one day
  // look like twelve.
  const dates = [
    "2026-08-17", "2026-08-18",
    ...Array.from({ length: 12 }, () => "2026-08-19"),
  ];
  const labels = tickLabels(dates, day, tickCapacity(880)); // the full-width card on the page
  const shown = labels.filter(Boolean);
  assert.deepEqual(shown, ["08-17", "08-18", "08-19"]);
  assert.equal(labels.length, dates.length, "every slot keeps its index");
  assert.equal(labels[7], "08-19", "the 12-point day is marked once, under the middle of its run");
});

test("labels that cannot fit without touching are dropped, not overlapped", () => {
  // Three single-point days at the head of a long same-day run sit one index apart. On a narrow
  // card there is no room for all three, and drawing them anyway smears them together.
  const dates = ["2026-08-17", "2026-08-18", ...Array.from({ length: 12 }, () => "2026-08-19")];
  const shown = tickLabels(dates, day, 4).filter(Boolean);
  assert.deepEqual(shown, ["08-17", "08-19"]);
});

test("a run's label sits at its midpoint, so it reads under the span it names", () => {
  const dates = ["a", "b", "b", "b", "b", "b"];
  const labels = tickLabels(dates, String, 10);
  assert.equal(labels[0], "a");
  assert.equal(labels[3], "b", "the 5-wide run is marked in its middle, not at its left edge");
  assert.equal(labels.filter(Boolean).length, 2);
});

test("labels thin to what the width can hold", () => {
  const dates = Array.from({ length: 30 }, (_, i) => `d${i}`);
  for (const room of [3, 6, 10, 30]) {
    const shown = tickLabels(dates, String, room).filter(Boolean);
    assert.ok(shown.length <= room, `${shown.length} labels must fit a capacity of ${room}`);
    assert.ok(shown.length >= 1, "thinning must never empty the axis");
  }
});

test("kept labels are always at least one gap apart", () => {
  // The property that actually prevents a smear: no two labels closer than width/capacity.
  const dates = Array.from({ length: 40 }, (_, i) => `d${Math.floor(i / 3)}`);
  const room = 8;
  const labels = tickLabels(dates, String, room);
  const at = labels.map((l, i) => (l ? i : -1)).filter((i) => i >= 0);
  const minGap = dates.length / room;
  at.slice(1).forEach((idx, n) => {
    assert.ok(idx - at[n] >= minGap, `labels at ${at[n]} and ${idx} are closer than ${minGap}`);
  });
});

test("a zero-width (pre-measure) chart still labels something", () => {
  assert.equal(tickCapacity(0), 1);
  assert.equal(tickCapacity(-10), 1);
  assert.equal(tickCapacity(NaN), 1);
  assert.equal(tickLabels(["a", "b"], String, tickCapacity(0)).filter(Boolean).length, 1);
});

test("distinct dates are never merged", () => {
  const dates = ["2026-08-17", "2026-08-18", "2026-08-19"];
  assert.deepEqual(tickLabels(dates, day, 10), ["08-17", "08-18", "08-19"]);
});

test("labelRuns groups only CONSECUTIVE equals", () => {
  const runs = labelRuns(["a", "a", "b", "a"]);
  assert.equal(runs.length, 3, "the later 'a' is its own run — the axis is a sequence, not a set");
  assert.deepEqual(runs.map((r) => [r.label, r.start, r.end]), [["a", 0, 1], ["b", 2, 2], ["a", 3, 3]]);
});

test("an empty series yields no labels and does not throw", () => {
  assert.deepEqual(tickLabels([], String, 5), []);
  assert.deepEqual(labelRuns([]), []);
});

/* ── the guard: no chart may reintroduce an auto-scaled axis ────────────────────────────────── */

const CHARTS = ["trend-chart-impl.tsx", "stacked-bar-impl.tsx", "multi-line-chart-impl.tsx"];
const HERE = dirname(fileURLToPath(import.meta.url));
/** Comments are stripped: a guard that reads prose fires on the note explaining the bug it
 *  guards against, which teaches the next author to delete the note. */
const chartSrc = (f: string) =>
  readFileSync(join(HERE, "..", "components", "shared", f), "utf-8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");

test("no analytics chart lets recharts choose its y range", () => {
  // recharts' string domains ("dataMin - 6", "auto") scale to the data's own extent, which is how
  // a 4-point wobble came to be drawn as a mountain. Both shipped in this directory.
  for (const file of CHARTS) {
    const src = chartSrc(file);
    for (const banned of ["dataMin", "dataMax", '"auto"', "'auto'"]) {
      assert.ok(
        !src.includes(banned),
        `${file} uses ${banned} — an axis that scales to its data misstates it`,
      );
    }
  }
});

test("every y-axis takes its domain, ticks and formatter from this module", () => {
  for (const file of CHARTS) {
    const src = chartSrc(file);
    assert.ok(src.includes('from "@/lib/chart-axis"'), `${file} must use the shared axis spec`);
    assert.ok(/domain=\{axis\.domain\}|domain=\{dom\}/.test(src), `${file}: domain must come from the spec`);
    assert.ok(/ticks=\{axis\.ticks\}|ticks=\{ticks\}/.test(src), `${file}: ticks must come from the spec`);
    assert.ok(/tickFormatter=\{axis\.format\}/.test(src), `${file}: tick text must come from the spec`);
  }
});

test("a score range cannot be overridden per call site", () => {
  // Every TrendChart in this app plots a 0–100 score, and the `domain` prop existed only to be
  // passed [0,100] nine times — or, one day, something else. The component owns the range now.
  const src = chartSrc("trend-chart-impl.tsx");
  assert.ok(!/domain\?:/.test(src), "TrendChart must not accept a domain override");
});

test("every x-axis de-duplicates its labels through the shared helper", () => {
  // Without this, a per-snapshot series repeats one date across the whole axis. MultiLineChart had
  // its own inline copy of this logic and the other two had none.
  for (const file of CHARTS) {
    const src = chartSrc(file);
    assert.ok(src.includes("tickLabels("), `${file} must build x labels via tickLabels`);
    assert.ok(/interval=\{0\}/.test(src), `${file}: tick index must stay aligned to row index`);
    assert.ok(!src.includes("minTickGap"), `${file}: pixel spacing does not de-duplicate repeats`);
  }
});
