# Analytics Dashboard — Axis Scaling Review

**Objective:** every score-based chart uses a consistent, meaningful scale; counts stay dynamic;
percentages stay 0–100%; each axis matches the semantics of its metric.

**Scope:** `web/app/(app)/analytics/page.tsx` and the three chart primitives it uses
(`trend-chart.tsx`, `stacked-bar.tsx`, `multi-line-chart.tsx`). No analytics calculation was
changed — this is axis configuration only. Every series is defined in
`examples/api_server.py :: build_analytics`.

## Per-chart matrix

| Chart | Metric type | Current axis | Recommended axis | Change | Reasoning |
|---|---|---|---|---|---|
| **Health Improvement** | Normalized score 0–100 (`int(overall)`) | **Auto** `[dataMin−6, dataMax+6]` | **`[0, 100]`** | ✅ **Fixed** | The only score chart that auto-scaled. On a narrow range (e.g. 46→58) the axis zoomed to ~`[40, 64]`, making a modest 12-point change fill the card and look like a dramatic swing. Pinning `[0, 100]` reads truthfully and matches the three diversity charts. |
| **Topic Diversity** | Normalized score 0–100 | `[0, 100]` | `[0, 100]` | — Already correct | Score metric; already pinned. |
| **Political Diversity** | Normalized score 0–100 (`viewpointBalance`) | `[0, 100]` | `[0, 100]` | — Already correct | Score metric; already pinned. |
| **Publisher Diversity** | Normalized score 0–100 (`sourceDiversity`) | `[0, 100]` | `[0, 100]` | — Already correct | Score metric; already pinned. |
| **Reading Volume** | Count (articles/day) | Dynamic `0…max` | Dynamic `0…max` | — Correct, unchanged | A count has no fixed ceiling; a fixed 0–100 would flatten real day-to-day variation. Bars anchor at 0. |
| **Recommendation Acceptance** | Count (accepted / ignored per day) | Dynamic `0…max` | Dynamic `0…max` | — Correct, unchanged | Same as Reading Volume — counts, so dynamic max is right. Bars anchor at 0. |
| **Emotional Tone** | Percentage shares | `[0, 1]` fixed, ticks 0/25/50/75/100% | `[0, 1]` | — Already correct | Shares of attention; a fixed 0–100% axis keeps every series on a common baseline. |
| **Reporting vs Opinion** | Percentage (100% stack; `reporting + opinion = 1.0`) | **Auto** (no explicit domain) | **`[0, 1]`** fixed, ticks 0/25/50/75/100% | ✅ **Hardened** | Data always sums to 1.0, so it *rendered* full-height already — but the axis wasn't explicitly pinned. Now `StackedBar` pins `[0, 1]` whenever `percent`, matching `MultiLineChart` so both percentage components behave identically and the axis is 0–100% by contract, not by luck. |

### Referenced but outside the Analytics page

| Chart | Where | Axis | Verdict |
|---|---|---|---|
| **Reading Distribution** | Report page (`BarList`, horizontal share bars) | Shares 0–1 by construction (no numeric Y-axis) | ✓ Inherently 0–100%; no change. |
| Score-history trend | Profile page (`TrendChart`) | `[0, 100]` | ✓ Already pinned (a score). |
| Health-trend sparkline | Dashboard hero (`TrendChart`, `showAxis={false}`) | No axis rendered (sparkline) | Left as-is — a sparkline shows relative movement and renders no axis/ticks; out of the Analytics scope. Flagged as an optional future consistency tweak, not changed here. |

## Changes made

1. **`web/app/(app)/analytics/page.tsx`** — Health Improvement `TrendChart` now passes `domain={[0, 100]}`.
2. **`web/components/shared/stacked-bar.tsx`** — the `YAxis` pins `domain={[0, 1]}` + ticks
   `[0, .25, .5, .75, 1]` **only when `percent`**; count charts keep recharts' dynamic `0…max`
   (the `percent` flag already drove the `%` tick formatting, so this is the same gate).

Net: the four normalized-score charts now share one fixed 0–100 scale, counts stay dynamic, and both
percentage charts are explicitly 0–100%.

## Before / after (Health Improvement)

Seeded with overall scores in a deliberately narrow band (46→58) — the case auto-scaling distorts most:

- **Before** — Y-axis auto-scaled to ~`[40, 64]`; the modest rise filled the whole card, reading as a
  large swing, and visually mismatched the diversity charts below (which use 0–100).
- **After** — Y-axis `[0, 100]` (0/25/50/75/100); the same data reads as a gentle rise near mid-scale,
  consistent with every other score chart.

## Validation

- `tsc --noEmit` clean · `next build` succeeds · `node --test` 89 passed.
- Before/after captured on the real stack (FastAPI engine + Next production build) with a seeded
  multi-day analytics history; all eight charts render with the axes above.
- No analytics calculation touched — `build_analytics` and every series are unchanged.
