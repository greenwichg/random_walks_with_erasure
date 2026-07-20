# End-to-End UX Review (RC1)

A first-time-user and returning-user walkthrough of the complete Information Health journey, grounded
in the current code. **Read-only review — no changes implemented.** No scoring algorithm or backend
calculation is in scope; every recommendation below is presentation/UX only.

Priority = severity of the UX impact (High / Medium / Low). Each item is classified **Must fix before
RC1** or **Nice to have after RC1**.

---

## Executive summary

The product is polished and unusually honest — shared Read/Save controls, transparent recommendation
evidence, a best-in-class Settings save flow, and (after the recent work) consistent chart axes and
metric empty states. The gaps that matter for RC1 cluster around **three themes**:

1. **The Estimate→Measured story is dropped after onboarding.** Onboarding carefully labels the result
   an "Estimate" and explains progressive unlocking; the in-app Dashboard and Health Report show no
   mode, no coverage, and no "read N more to unlock" guidance — and the Report unconditionally renders
   a measured-only field (`axisConfidence`), risking `NaN%` for an estimate.
2. **A few touch/mobile affordances are hover-only** (dismiss ✕ on recommendation cards; metric/chart
   explanation tooltips), so real actions and explanations disappear on phones.
3. **One fabricated value** — the sidebar's hardcoded "12 days strong" streak — contradicts the real
   streak on the Dashboard and undercuts the product's "never fabricate" principle.

Everything else is small polish. Full per-page detail follows.

### Must fix before RC1

| # | Page | Issue | Priority |
|---|---|---|---|
| CC2 | Global (sidebar) | Hardcoded "12 days strong…" streak shown to every user; contradicts the real Dashboard streak | High |
| RE1 | Recommendations | Card dismiss ✕ is `opacity-0 → group-hover` — invisible/unusable on touch | High |
| R1 | Health Report | No Estimate/coverage banner; `axisConfidence` rendered unconditionally → `NaN%` risk on an estimate | High |
| D2 | Dashboard | Estimate mode + "reads to Measured" progress not surfaced (no progressive-unlock guidance for a new user) | High |
| CC1 | Global (header) | Duplicate page title → two `<h1>` per page on desktop (a11y + visual redundancy) | Medium |
| D1/A2 | Dashboard / Analytics | Metric & chart explanations are hover-only tooltips → unavailable on touch | Medium |

### Nice to have after RC1

CC3 (hardcoded English captions bypass i18n on Report + metric cards), CC4/RE4 (empty-state screens
without a forward CTA on Saved & Recommendations), RE2 (icon-only rec actions vs labeled Save/Read),
RE3 (Read-Later has no destination surface — verify), O1 (step indicator only mid-flow), SI1 (no
pending state on the sign-in button), D3 (Dashboard trend sparkline auto-scales), A1 (no first-run
guidance on empty Analytics), A3 (no date-range control), S1 (theme saves instantly but language needs
Save).

---

## 1. Onboarding

### Sign in
**Current UX** — One clean card, single provider (Google in prod, a demo login in the Colab build),
localized copy, brand mark. The middleware routes unauthenticated visitors here and returns them to
`callbackUrl` on success.

**Issues**
- **SI1** — The provider button has no pending/disabled state while the OAuth round-trip is in flight;
  on a slow network the click appears to do nothing. *Priority: Low.*

**Suggested** — Disable + spinner the button on click (`signIn` is already awaited in demo mode).
*Nice to have after RC1.*

### Onboarding flow (value → pick → build → Estimate)
**Current UX** — A genuinely good progressive flow: a value screen, an outlet picker (≥3, with a
spread "sample" shortcut), an animated build beat, then the **Initial Estimate** — explicitly badged
"Estimate", with a "your one thing" takeaway, a "what this is / isn't" panel, and a progressive
"Save & track" that defers sign-in until after the user has seen value. An anonymous single-article
analyzer is offered as a zero-commitment escape hatch. First impression is strong and honest.

**Issues**
- **O2 (High, cross-ref D2/R1)** — The careful **Estimate framing is lost the moment the user signs
  in.** Inside the app the Dashboard and Report don't repeat the "this is an estimate / here's how to
  reach a measured report" message. The single most valuable onboarding idea evaporates at the
  hand-off.
- **O1 (Low)** — The step indicator (`1 · 2 · 3`) only appears on the Pick and Build screens; the
  Welcome (logically step 1) and the final Estimate screen show none, so progress dots blink in
  mid-flow and vanish at the end.

**Suggested** — Carry the estimate/coverage state into the app (see D2). Show the step indicator on all
steps or none. *O2 Must fix; O1 Nice to have.*

---

## 2. Reading Experience

**Current UX** — Reading, saving, and recording are unified: one `ReadArticleButton` and one
`SaveButton` are reused across Recommendations, Discover, Search, Stories, and Saved, so behaviour is
identical everywhere. Read records into the canonical `/api/me/reads` pipeline first (in-app tracking),
then opens the real publisher URL; Save is optimistic with rollback and mirrors the profile counter.
Recommendation cards are transparency-first: a strategy badge ("Bridging / Discovery / For you / Same
story" — friendly, not jargon), an evidence "receipt" (You read X → Compare with Y), a **Why?** drawer
of real recommender evidence, and five feedback actions. Both primary controls show confirmation states
("Opened ✓", "Saved").

**Issues**
- **RE1 (High, Must fix — mobile)** — The card **dismiss ✕ is `opacity-0` until `group-hover`**, so on
  touch devices it never appears. Ignoring a recommendation — a core action, and the thing that
  persists the "ignore" — is unreachable on a phone.
- **RE2 (Medium)** — Why? / Read-later / Like / Dislike are **icon-only buttons with hover-only
  tooltips**; on mobile a sighted user sees four unlabeled glyphs, and it's inconsistent with the
  *labeled* Save/Read buttons on the same card. (Screen-reader `aria-label`s are present — this is a
  sighted-touch discoverability gap.)
- **RE3 (Medium, verify)** — "Read Later" records `read_later` feedback but there is **no destination
  surface** for it: the Saved page is a separate pipeline, so a user who taps Read-Later has no list to
  find those items in. Either point Read-Later at Saved or add a Read-Later view.
- **RE4 (Medium, cross-ref CC4)** — The Recommendations empty state ("You're all caught up") offers no
  next step (no link to Discover/Stories).
- **Feedback clarity** — Like/Read-later communicate only via icon-color toggle; Dislike removes the
  card (clear). No toast/announcement. Acceptable, but the quietest of the flows.

**Suggested** — Make dismiss visible on touch (e.g. always-visible at `sm`, or a persistent overflow
menu). Give the icon actions labels or a first-run legend. Resolve Read-Later's destination. Add a CTA
to the empty state. *RE1 Must fix; RE2/RE3/RE4 Nice to have (RE3 pending verification).*

---

## 3. Dashboard

**Current UX** — A strong at-a-glance home: hero **Information Health Score** ring + band badge +
month delta, a data-driven headline naming the reader's strongest metric and biggest opportunity, a
30-day health-trend sparkline, four "today" stat cards (articles, reading time vs goal, political
share, streak), and the eight metric cards — now with the **"Not enough data yet" empty state** for
unmeasurable metrics (never a fake 0). Clear CTAs to the Report and Coach.

**Issues**
- **D2 (High, Must fix)** — **No progressive-unlocking guidance.** `DashboardSummary` carries no
  `mode`/`coverage`, so a freshly-onboarded user sees metric cards with no indication they're an
  Estimate, no "read 5 articles to unlock your Measured report" progress, and no through-line from
  onboarding. The per-metric empty states say "not enough data yet" but nothing at the page level
  orients the user. (Surfacing the existing mode/coverage is metadata, not a scoring change.)
- **D1 (Medium, Must fix — mobile)** — The **only** per-metric explanation is the header
  `InfoTooltip`, which is hover-based; on touch there is effectively no way to read what a metric
  means from the Dashboard. ("Missing explanations" is a named review criterion.)
- **D3 (Low/Medium)** — The health-trend **sparkline auto-scales** (`showAxis={false}`, no fixed
  0–100), so a small score wobble can look dramatic — the same class of issue just fixed on Analytics.
- **D4 (cross-ref CC1)** — Duplicate page title.

**Suggested** — Add an Estimate/coverage strip with a "reads to Measured" progress (reuse the
onboarding language); make the metric info open on tap/focus; pin the sparkline to 0–100. *D1/D2 Must
fix; D3 Nice to have.*

---

## 4. Analytics

**Current UX** — Eight charts over stored data, now with **consistent, semantically-correct axes**
(score charts fixed 0–100, counts dynamic, percentages 0–100% — per the recent axis review), honest
empty series (no fabrication), legends that never rely on colour alone, and a dark-mode contrast fix
already in place. Solid and trustworthy.

**Issues**
- **A1 (Medium)** — A **first-time / day-1 user sees near-empty analytics** (one snapshot → flat or
  empty trends) while the subtitle promises "the last 30 days," with no "come back after a few days"
  framing. The empty state is honest but unguided.
- **A2 (Medium, cross-ref D1)** — Chart `info` tooltips are hover-only → unavailable on touch.
- **A3 (Low)** — Fixed "last 30 days"; no date-range control.
- **Scope note** — The brief lists **Reading Distribution** under Analytics, but it lives on the
  **Health Report** (a `BarList` of topic/source shares, inherently 0–100%). Worth aligning IA
  expectations.

**Suggested** — A one-line first-run hint on empty charts; tap/focus-accessible chart info. *All Nice
to have.*

---

## 5. Health Report

**Current UX** — The flagship analysis: score ring + band + delta + axis-confidence, a six-metric
radar (now gapping unavailable metrics rather than plotting fake 0s), political distribution +
spectrum bar, attention profile, topic & source distributions, blind spots, ranked improvements
(with "Add to goals"), and an expandable per-metric breakdown (with the empty-state treatment).
Genuinely deep and well-organized.

**Issues**
- **R1 (High, Must fix)** — **No Estimate/coverage/mode banner, and `axisConfidence` is rendered
  unconditionally** (`Math.round(report.axisConfidence * 100)%`). `axisConfidence` is a *measured-only*
  field; if a signed-in user below the read threshold gets an estimate report, this renders **`NaN%`**
  and the whole page reads as "measured" with no honest label. Needs both a mode banner and a guard.
  *(Verify the exact estimate path for a signed-in sub-threshold user.)*
- **R2 (Medium, cross-ref CC3)** — The data-driven captions (`tiltText`, `dietSummary`, "You read X%
  left, Y% center, and Z% right") and the metric cards' "Typical reader:" / "Your value:" are
  **hardcoded English**, bypassing the i18n system the rest of the app uses — so es/fr/de/pt readers
  see English on the flagship report.
- **R3 (Low, verify)** — Confirm the improvements' "Add to goals" affordance gives explicit feedback.
- **R4 (cross-ref CC1)** — Duplicate page title.

**Suggested** — Add the Estimate banner + guard `axisConfidence`; route the captions and metric labels
through `t()`. *R1 Must fix; R2 Nice to have (High if non-English is an RC1 market).*

---

## 6. Settings

**Current UX** — The strongest flow in the product. A draft/base model diffs the working copy against
the snapshot it was seeded from, so background refetches never create phantom "unsaved" and never
clobber edits; Save sends a **minimal PATCH**; a floating save bar reflects the **entire lifecycle**
(unsaved → saving → saved, or failed with Retry) with `role="status" aria-live="polite"`. Theme applies
instantly and write-throughs to the account (with cross-device restore); sliders carry plain-language
labels; the privacy section honestly links to the published policy instead of dead toggles; the
extension connect uses real per-user tokens. Feedback here is exemplary.

**Issues**
- **S1 (Low)** — **Theme saves instantly, but Language requires the Save button** — two Appearance
  controls with different mental models. Minor but slightly confusing.
- **S3 (Low, verify)** — Confirm the Radix sliders announce their current value (aria) to screen
  readers; the visible value chip is not automatically an accessible name.

**Suggested** — Either make Language write-through like Theme, or visually group the deferred settings
apart from the instant Theme control. *Nice to have.*

---

## Cross-cutting findings

| # | Finding | Where | Priority | Class |
|---|---|---|---|---|
| **CC1** | **Duplicate `<h1>`** — the header renders the page title (`hidden … sm:block`) *and* each page renders its own `<h1>`, so desktop shows the title twice and every page has two `h1` landmarks. | `components/layout/header.tsx` + every page | Medium | Must fix |
| **CC2** | **Fabricated streak** — the sidebar promo hardcodes *"12 days strong. Read one cross-cutting article today to keep it alive."* for every user, contradicting the real streak stat on the Dashboard. | `lib` catalog `sidebar.streakBlurb` + `components/layout/sidebar.tsx` | High | Must fix |
| **CC3** | **Hardcoded English bypasses i18n** on the Report captions and the metric cards' "Typical reader:" / "Your value:". | `app/(app)/report/page.tsx`, `components/shared/metric-card.tsx`, `components/report/metric-accordion.tsx` | Medium | Nice to have* |
| **CC4** | **Empty states without forward guidance** — History's first-run empty state has a Discover CTA, but Saved and Recommendations empty states are dead ends. | `saved/page.tsx`, `recommendations/page.tsx` | Medium | Nice to have |

\* CC3 becomes High if a non-English locale is an RC1 launch market.

**Consistency wins already in place** (worth preserving): one shared Read/Save control set; one
`EmptyState`/`ErrorState` with retry; token-based theming; friendly strategy names; the metric
empty-state pattern; honest "never fabricated" empty series.

---

## Validation matrix

| Dimension | Assessment | Notable items |
|---|---|---|
| **Desktop** | Strong across all six flows. | Only cosmetic double-title (CC1). |
| **Mobile / touch** | Mostly responsive (drawer nav, stacked grids, measured-width charts), but **hover-only affordances break on touch**. | **RE1** (dismiss ✕ invisible), **D1/A2** (tooltip explanations), **RE2** (icon-only actions). |
| **Dark mode** | Systematically supported via CSS-variable tokens + `next-themes` (`attribute="class"`), theme-color meta set, a prior dark chart-contrast bug already fixed. | Spot-check the amber "Estimate" badge and chart fills on the dark surface. |
| **Accessibility** | Good baseline: `aria-label` on icon buttons, `aria-pressed`, `aria-live` save bar, visible focus rings, `<html lang>` synced to the active locale client-side. | **CC1** (double h1); tooltips not tap/focus-reachable (**D1/A2/RE2**); verify slider value announcements (**S3**) and "Add to goals" feedback (**R3**). |

---

## Recommended RC1 cut line

Ship-blocking (small, high-value): **CC2** (fabricated streak), **RE1** (touch dismiss), **R1**
(estimate banner + `axisConfidence` guard), **D2** (progressive-unlock guidance), **CC1** (double h1),
**D1/A2** (tap-accessible explanations). Everything else is post-RC1 polish. None of the above touches
scoring or backend calculations — all are presentation-layer changes.
