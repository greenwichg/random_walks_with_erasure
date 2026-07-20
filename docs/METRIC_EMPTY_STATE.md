# Metric Empty State

When a health metric cannot be measured yet — there isn't enough of the reader's activity to compute
it reliably — the Information Health dashboard no longer **hides** the metric card. It keeps the card
in place and shows a consistent *"Not enough data yet"* empty state inside it, with a single primary
CTA to generate the activity that unlocks the metric.

This document records the design decision, the availability condition, the components, the
accessibility contract, and the validation.

---

## The availability condition — an explicit backend signal, never `score === 0`

The trigger is an **explicit field on the metric**, decided by the backend (the only place that knows
whether a real score could be computed). The frontend never infers "unavailable" from the score.

Each metric in a report now carries:

```jsonc
{ "key": "openMindedness", "score": 0, "delta": 0, "band": "Unknown",
  "available": false, "reason": "insufficient_data", "minimumActivity": 5 }
```

- `available: false` ⇔ the backend could **not** compute a real score for this metric from the
  reader's activity. This is exactly the set of metrics the serializer previously **dropped**
  (e.g. Open-Mindedness before any cross-cutting recommendation reception; Viewpoint Balance / Echo
  Chamber without political reads; Confidence / Open-Mindedness on an outlets-only estimate).
- `reason` is a stable code (`"insufficient_data"`); `minimumActivity` is the read threshold that
  typically unlocks measurement (informational).
- A metric that the backend **did** compute carries `available: true` — including a genuine `score: 0`.
  A real 0 (`available: true, score: 0`) and an unmeasurable metric (`available: false`) are distinct,
  so **the empty state is never inferred from `score === 0`** (requirement #5).

Older payloads without the field are treated as `available: true` (the defaults on `MetricModel` and
the `Metric` type keep them valid) — the change is additive.

### What did *not* change

No scoring logic or metric calculation was touched. `_unavailable_metric()` is metadata only — it
carries no fabricated score (`score` is a neutral placeholder the UI never renders, `band` is
`"Unknown"`). The overall score and the "improvements" suggestions are computed from the **available**
metrics exactly as before (the previously-dropped metrics never contributed to either), so both are
byte-for-byte unchanged. Un-hiding replaces dropping; nothing shown before is altered.

---

## Backend (`examples/`)

| File | Change |
|---|---|
| `api_server.py` | `_unavailable_metric(key)` helper. Both serializers (`_serialize_report` measured + `estimate`) now **append an unavailable metric instead of `continue`-ing past it**; computed metrics get `available: true`. The estimate also emits Confidence as unavailable. `overall`/`improvements` filter to `available` metrics (identical result to before). |
| `api_fastapi.py` | `MetricModel` gains `available: bool = True`, `reason`, `minimumActivity` (defaults keep old payloads valid). |

The dashboard summary reuses `report["metrics"]` verbatim, so the flag flows to the dashboard with no
extra endpoint work.

## Frontend (`web/`)

| File | Change |
|---|---|
| `components/shared/metric-empty-state.tsx` | **New reusable component.** Renders the empty-state body: title, description, and the "Explore Articles" CTA (reuses the existing Discover nav → `/discover`). `showCta` prop for surfaces where a link cannot nest. |
| `components/shared/metric-card.tsx` | When `metric.available === false`, keeps the header (icon, label, info tooltip) and swaps the score/bar/benchmark for `<MetricEmptyState />`. An empty-state card is not itself a link (its CTA is the action → no nested links). |
| `app/(app)/page.tsx` | The eight-card grid always renders all eight cards (never `null`); the hero "biggest opportunity" copy considers measured metrics only. |
| `components/report/metric-accordion.tsx` | Empty-state row shows "Not enough data yet" in place of the score/bar; the expanded panel shows the description + CTA. |
| `components/shared/metric-radar.tsx` | Plots a gap (`null`) for an unavailable metric — never a fabricated `0`. |
| `types/domain.ts` | `Metric` gains `available?` / `reason?` / `minimumActivity?`. |
| `messages/` | `metric.emptyState.{title,description,cta}` in all five locales (en/es/fr/de/pt). |

### Copy (en)

- **Title** — "Not enough data yet"
- **Description** — "Continue exploring articles, saving stories, and reading recommendations. This
  insight will be available once there's enough activity to measure it reliably."
- **CTA** — "Explore Articles" → `/discover`

---

## Accessibility notes

- **Heading hierarchy preserved.** The empty state introduces **no new heading level**. The card's
  metric label stays a `<span>` (the card's de-facto heading, identical to populated cards) and the
  empty-state title is a `<p>`, so the page's `h1` (Dashboard) → `h3` (section) structure is unchanged
  and no heading level is skipped or added on only some cards.
- **Screen-reader readable.** The empty state is plain semantic text, read in order after the card's
  metric label — e.g. *"Open-Mindedness … Not enough data yet … Continue exploring articles …"* —
  giving full context. Nothing meaningful is conveyed by an icon alone: the CTA's compass icon is
  decorative and the CTA carries the visible text label "Explore Articles".
- **Keyboard navigation intact.** The only focusable control in an empty-state card is the CTA — a
  real anchor (`next/link`) in the natural tab order with the app's standard focus-visible ring. To
  avoid an interactive-element-in-interactive-element trap, an empty-state card drops the whole-card
  link it would otherwise carry, and in the report accordion the CTA lives in the expanded panel
  (not inside the row `<button>`).
- **"Waiting", not an error.** Muted styling and calm copy communicate that the metric is waiting for
  more activity. There is no `alert`/`status` live region and no error color, so assistive tech does
  not announce a failure.
- **Not color-dependent.** The state is legible from text alone; it does not rely on color.

---

## Validation

| Check | Result |
|---|---|
| `pytest tests/` (engine) | **1,335 passed** (contract updated: metrics are now emitted-and-flagged, not dropped; added `test_unavailable_metric_contract`). |
| `tsc --noEmit` (web) | clean |
| `check:i18n` | 636 keys × 5 languages, no unused keys |
| `node --test` (web) | 89 passed |
| `next build` (web) | succeeds |
| Playwright, real stack | Before/after captured — dashboard, card close-up, report accordion. |

**Before / after (real stack, a measured reader with no recommendation reception):**

- **Before** — the dashboard shows **seven** cards; Open-Mindedness is silently dropped.
- **After** — the dashboard shows **eight** cards; Open-Mindedness is the "Not enough data yet"
  empty-state card, sitting beside populated cards. Topic Diversity in the same grid shows a genuine
  `0 / 100` (a real score), demonstrating that the empty state is driven by the `available` flag and
  not by `score === 0`.
