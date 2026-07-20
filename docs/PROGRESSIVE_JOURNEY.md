# Progressive Information Health Journey

A consistent Estimate → Measured experience: from a reader's first visit through unlocking a fully
measured Information Health profile, every surface now states whether they're viewing an **Estimate**
or a **Measured** profile, shows honest **coverage** progress, and explains what unlocks each
unavailable insight.

**No scoring algorithm or analytics calculation was changed.** Everything here is metadata plumbing
(surfacing `mode`/`coverage` the engine already computes) plus presentation.

---

## The lifecycle

| Stage | When | What the reader sees |
|---|---|---|
| **Estimate** | Onboarded, below the read threshold (`reads < 5`) | An "Estimate" badge + "Building your Information Health profile", a reads-toward-threshold progress bar (`N of 5 reads`), the remaining count, and an "Explore Articles" CTA — on the Dashboard, the Health Report header, and Analytics. Metric cards that can't be measured yet explain **what action unlocks them** and (for read-gated metrics) show `N of 5` progress. |
| **Measured** | `reads ≥ 5` | A compact "✓ Measured · based on N reads · Confidence X%" chip keeps the context visible without taking space. Measured-only detail (axis confidence) renders normally. |

The onboarding flow already labels the Estimate; this workstream carries that context **into the app**
so it never disappears after sign-in.

## Coverage messaging (the exact copy)

| Key | English |
|---|---|
| `coverage.estimate.badge` | Estimate |
| `coverage.measured.badge` | Measured |
| `coverage.building.title` | Building your Information Health profile |
| `coverage.progress` | `{reads} of {threshold} reads` |
| `coverage.remaining` | `{n} more to unlock your Measured profile` |
| `coverage.measured.basedOn` | `based on {reads} reads` |
| `coverage.confidence` | `Confidence {pct}%` |
| `unlock.reads` | Read a few more articles to unlock this insight. |
| `unlock.political` | Read political articles from more than one side to unlock this. |
| `unlock.reception` | Open a recommendation that crosses your usual viewpoint to unlock this. |

All ten strings are localized in **all five languages** (en/es/fr/de/pt) and validated by `check:i18n`.
Only backend-supported progress is shown: the `reads`/`threshold` coverage is real; there is no
per-metric political-read count in the contract, so the political/reception metrics show their unlock
**action** but not a fabricated count.

## Progressive unlocking (per-metric)

The metric empty state now answers all three questions the review asked for:

- **Why** it's unavailable — "Not enough data yet" (driven by the backend `available: false` flag,
  never inferred from `score === 0`).
- **What unlocks it** — a metric-specific hint (`METRIC_UNLOCK`): read-derived metrics → read more;
  Echo/Viewpoint → read political articles from more than one side; Open-Mindedness → open a
  cross-cutting recommendation.
- **Current progress** — `N of 5 reads`, shown only for the read-gated metrics it actually applies to.

## What changed

### Backend (metadata only — `examples/`)
- `build_dashboard` now carries `mode` + `coverage`, lifted verbatim from the reader's report.
- `build_analytics` now carries `coverage` (real read count toward the threshold).
- `_report_for`: a signed-in **Estimate** now reports the reader's **real** `coverage.reads` (was a
  hardcoded 0), so "N of 5" progress is honest. The anonymous estimate path is unchanged (0 reads).
- `DashboardModel` += `mode`/`coverage`; `AnalyticsModel` += `coverage`.
- Tests: `test_signed_in_estimate_carries_accurate_coverage` (estimate ⇒ `mode='estimate'`, **no
  `axisConfidence`**, accurate `coverage.reads`), plus dashboard/analytics contract updates.

### Frontend (`web/`)
- **`lib/coverage.ts`** — one source of truth: `coverageStatus(mode, coverage)` (prefers backend
  `mode`, falls back to `coverage.sufficient`) + the `METRIC_UNLOCK` map. Unit-tested (`coverage.test.ts`).
- **`ProfileProgress`** — the shared Estimate/Measured banner, used by the Dashboard, Health Report,
  and Analytics.
- **Health Report** — the banner in the header, and **`axisConfidence` is guarded** to render only for
  a measured report. The service now types `/report` as the honest `HealthReport` union (was
  `MeasuredHealthReport`), which forces the guard.
- **Metric cards / accordion** — pass the metric key + coverage into the empty state for the
  per-metric unlock hint + progress.
- Message catalogs (×5) + `DashboardSummary`/`AnalyticsSeries` types gain the optional fields.

## Before / after

Captured on the real stack — an **Estimate reader** (onboarded, 2 reads) and a **Measured reader**
(6 reads):

| Surface | Before | After |
|---|---|---|
| **Health Report (estimate)** | Renders **"Axis confidence NaN%"** and looks identical to a measured report — no indication it's an estimate. | An "Estimate · Building your profile · 2 of 5 reads · 3 more to unlock" banner; the axis-confidence row is **hidden** (measured-only). |
| **Dashboard (estimate)** | Metrics with no context; unavailable cards say only "not enough data yet". | The Estimate banner up top; empty-state cards now say **what** unlocks them (e.g. Open-Mindedness → "Open a recommendation that crosses your usual viewpoint") and read-gated ones show "2 of 5 reads". |
| **Report / Dashboard (measured)** | No mode indicator. | A compact "✓ Measured · based on 6 reads" chip keeps the context; confidence renders normally. |

## Consistency & mobile

- **Terminology** is centralized: "Estimate", "Measured", "Coverage" (`N of M reads`), and
  "Confidence" come from the shared catalog keys and `coverageStatus`, so every page reads the same.
- **Mobile**: the estimate banner stacks its CTA below the text at `< sm`; the measured chip wraps;
  the progress bar carries `role="progressbar"` + `aria-valuenow/min/max`. Verified in the captures.

## Validation

- Engine `pytest`: **1,337 passed** (+2 new contract tests).
- Web: `tsc` clean · `check:i18n` 646 keys × 5, no unused · `node --test` **96 passed** (+7 for
  `coverage.ts`) · `next build` succeeds.
- Real-stack before/after captured for estimate + measured readers (Dashboard + Report).
